### Title
Unauthenticated, unbounded `seconds` parameter on LOOP plugin pprof proxy enables long-lived connection/goroutine exhaustion - ([File: core/web/loop_registry.go])

### Summary
The `pprofURLVals` function parses the `seconds` query parameter with `strconv.Atoi` and uses it directly to compute a `context.WithTimeout` duration with no upper bound, and the resulting request is proxied through `doRequest` to an internal LOOP plugin's `/debug/pprof/*` endpoint. Because the `/plugins/:name/debug/pprof/*profile` and `/plugins/:name/debug/pprof/symbol` routes are registered on the unauthenticated `api` route group in `core/web/router.go` (unlike `/debug` and `/v2` routes, which are wrapped in `auth.Authenticate`), an unauthenticated attacker can hold open proxied HTTP connections/goroutines for arbitrarily long, attacker-chosen durations.

### Finding Description
`pprofURLVals` in `core/web/loop_registry.go` reads the `seconds` query value and computes: [1](#0-0) 
`timeout` is not clamped to any maximum. `pluginPPROFHandler` forwards this timeout into `doRequest`, which builds `ctx, cancel := context.WithTimeout(gc.Request.Context(), timeout)` and issues the outbound request via `http.DefaultClient.Do(req)` with no separate client-side timeout: [2](#0-1) 

Contrary to the literal "integer overflow" framing in the question, `strconv.Atoi` returns an error for unparseable/overflowing numeric strings, in which case the code falls back to the safe default `PPROFOverheadSeconds * time.Second` (30s) — so a huge numeric string like `99999999999999999999` does **not** produce an oversized timeout. However, an attacker does not need overflow: any *valid* large `int` (e.g. `seconds=315360000` for 10 years) parses successfully via `Atoi` and is used verbatim, producing a multi-year `context.WithTimeout`. Since the underlying pprof endpoint on the plugin side (`net/http/pprof.Profile`) blocks for the requested `seconds` duration doing CPU profiling, the proxied connection, the goroutine serving this gin request, and the outbound TCP connection to the LOOP plugin can all be held open for that entire duration.

Critically, these routes are wired without any authentication check. In `core/web/router.go`, `loopRoutes(app, api)` is registered on the base `api` group, which only has rate-limiting and session middleware, unlike `debugRoutes` and `v2Routes` which wrap their groups in `auth.Authenticate(...)`: [3](#0-2) [4](#0-3) 

This means an unprivileged, unauthenticated attacker can reach `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler` directly.

### Impact Explanation
Repeated requests with a large `seconds` value each tie up a gin request goroutine, an outbound TCP connection, and (transitively) plugin-side resources for the requested duration. With no upper bound and no authentication gate, an attacker can issue many such requests concurrently to exhaust the node's HTTP handler/goroutine pool and outbound connection capacity, degrading or denying legitimate node HTTP functionality (API, GraphQL, health checks) — a scoped, low-cost denial-of-service against node availability.

### Likelihood Explanation
Feasibility is high: the endpoint requires no authentication, no rate-limit is specific to this handler beyond the generic authenticated-period limiter applied to the whole `api` group, and a single crafted GET request (`/plugins/<name>/debug/pprof/profile?seconds=<large>`) is sufficient per connection held. Repeatability is straightforward by issuing multiple concurrent requests. The only precondition is that at least one plugin is registered in the `LoopRegistry` (`p, ok := l.registry.Get(pluginName)`), which is standard for a running LOOP-based node.

### Recommendation
- Add authentication middleware to `loopRoutes` (mirroring `debugRoutes`/`v2Routes` use of `auth.Authenticate`), so these internal debug/pprof proxy endpoints require a valid session/token.
- Clamp `seconds` in `pprofURLVals` to a safe maximum (e.g. reuse `PPROFOverheadSeconds` bound or a configurable ceiling like 60s), rejecting or capping values above the maximum instead of passing them through unchecked.
- Set an explicit timeout on the `http.Client` used in `doRequest` independent of the derived context, and consider adding a dedicated rate limit for pprof-proxy routes.

### Proof of Concept
Unit test for `pprofURLVals`:
```go
func TestPprofURLVals_ClampsSeconds(t *testing.T) {
    gc, _ := gin.CreateTestContext(httptest.NewRecorder())
    gc.Request = httptest.NewRequest("GET", "/x?seconds=315360000", nil) // 10 years
    _, timeout := pprofURLVals(gc)
    assert.LessOrEqual(t, timeout, time.Duration(maxAllowedSeconds+PPROFOverheadSeconds)*time.Second)
}
```
Expected (current, failing) behavior: `timeout` equals `(315360000+30) * time.Second`, i.e., unbounded.

Integration test plan: register a stub LOOP plugin whose `/debug/pprof/profile` handler blocks until its context is cancelled or `seconds` elapses; issue N concurrent unauthenticated requests to `/plugins/<name>/debug/pprof/profile?seconds=<large>` against the router built by `NewRouter`; assert goroutine count (`runtime.NumGoroutine()`) grows and stays elevated proportional to N and does not return to baseline within a short window, demonstrating connection/goroutine pinning without requiring authentication.

### Citations

**File:** core/web/loop_registry.go (L140-146)
```go
	timeout = PPROFOverheadSeconds * time.Second
	if sec, ok := gc.GetQuery("seconds"); ok {
		urlVals.Set("seconds", sec)
		if i, err := strconv.Atoi(sec); err == nil {
			timeout = time.Duration(i+PPROFOverheadSeconds) * time.Second
		}
	}
```

**File:** core/web/loop_registry.go (L150-215)
```go
func (l *LoopRegistryServer) pluginPPROFHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/"+gc.Param("profile"), l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	l.logger.Infow("Forwarding plugin pprof request", "plugin", pluginName, "url", pluginURL)
	l.doRequest(gc, "GET", pluginURL, nil, timeout, pluginName)
}

func (l *LoopRegistryServer) pluginPPROFPOSTSymbolHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/debug/pprof/symbol", l.loopHostName, p.EnvCfg.PrometheusPort)
	urlVals, timeout := pprofURLVals(gc)
	if s := urlVals.Encode(); s != "" {
		pluginURL += "?" + s
	}
	body, err := io.ReadAll(gc.Request.Body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error reading plugin pprof request body: %s", err))
		return
	}
	l.doRequest(gc, "POST", pluginURL, bytes.NewReader(body), timeout, pluginName)
}

func (l *LoopRegistryServer) doRequest(gc *gin.Context, method string, url string, body io.Reader, timeout time.Duration, pluginName string) {
	ctx, cancel := context.WithTimeout(gc.Request.Context(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin pprof request: %s", err))
		return
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		msg := "plugin pprof handler failed to post plugin url " + html.EscapeString(url)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q pprof", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
}
```

**File:** core/web/router.go (L87-91)
```go
	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
```

**File:** core/web/router.go (L230-236)
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
	loopRegistry := NewLoopRegistryServer(app)
	r.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
	r.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
	r.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
	r.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
