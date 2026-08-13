### Title
Unauthenticated resource-exhaustion via plugin pprof proxy - ([File: core/web/loop_registry.go])

### Summary
`loopRoutes` mounts `/plugins/:name/debug/pprof/*profile` on the base `api` group without any `auth.Authenticate` middleware, unlike every other sensitive route (e.g. `debugRoutes`, `authv2`, `metricRoutes`). An unprivileged network caller can therefore invoke `pluginPPROFHandler`, which forwards the request to the LOOP-p plugin's pprof server and holds the connection open for up to `seconds + 30` seconds, controlled entirely by an attacker-supplied `seconds` query parameter.

### Finding Description
`loopRoutes` in `core/web/router.go` registers the plugin pprof endpoints directly on `r` (the `api` group), with no `auth.Authenticate(...)` wrapper: [1](#0-0) 

Compare this to `debugRoutes`, which explicitly requires session authentication for the in-process `/debug/vars` endpoint: [2](#0-1) 
and to `metricRoutes(authv2)`, which is only reachable behind full `auth.Authenticate` + role checks: [3](#0-2) [4](#0-3) 

The only middleware applied to the `api` group as a whole is a generic rate limiter and session store, not an auth check: [5](#0-4) 

In `pluginPPROFHandler`, the caller-controlled `seconds` query parameter is parsed and used directly to compute the request timeout with no upper bound: [6](#0-5) [7](#0-6) 

`doRequest` then issues an outbound HTTP request to the internal LOOP-p pprof server using `http.DefaultClient` (an unbounded, shared client with no connection-pool limits configured) and blocks the gin handler goroutine, and the underlying TCP connection to the LOOP-p process, for the full computed timeout: [8](#0-7) 

Because the route is unauthenticated, any external caller who can reach the node's HTTP port can supply an arbitrary plugin `name` that resolves via `registry.Get` and a large `seconds` value (e.g. `seconds=3600`), and open many such requests concurrently. Each request occupies a gin worker goroutine and an outbound connection to the LOOP-p plugin for the full duration, with no server-side cap on `seconds`, no per-route rate limit distinct from the generic global limiter, and no concurrency limit on in-flight forwarded pprof requests. The generic `Authenticated`/`AuthenticatedPeriod` rate limiter (default 1000 req/min) is applied uniformly to authenticated and unauthenticated traffic alike on this group, but it limits request *count*, not request *duration* — so a small number of long-`seconds` requests already exhausts server capacity well before hitting the count-based limit.

### Impact Explanation
An attacker who can reach the node's web server (even without any session, API key, or role) can hold open many long-lived proxied HTTP connections to the LOOP-p pprof endpoint, tying up gin worker goroutines and TCP connections. Because there's no dedicated concurrency bound or maximum-`seconds` clamp, a modest number of concurrent requests with large `seconds` can exhaust available server capacity, degrading or denying node HTTP functionality (job management, GraphQL, health checks) for legitimate operators — a denial-of-service impact.

### Likelihood Explanation
Highly feasible: the endpoint requires no authentication or authorization at all (confirmed by its exclusion from any `auth.Authenticate` middleware in `router.go`), only knowledge of a registered plugin name (discoverable via the also-unauthenticated `/discovery` and `/plugins/:name/metrics` endpoints) and an HTTP client capable of sending a GET with a `seconds` query param. Repeated exploitation is trivial and requires no state or race condition.

### Recommendation
- Require authentication (at minimum `auth.Authenticate(..., auth.AuthenticateBySession)`, ideally with an appropriate role) on `loopRoutes`, consistent with `debugRoutes` and `metricRoutes`.
- Clamp/validate the `seconds` parameter to a sane maximum (e.g. matching pprof's own defaults) before computing `timeout` in `pprofURLVals`.
- Add an explicit concurrency limit (e.g. semaphore) for in-flight `doRequest` calls in `LoopRegistryServer`, and use a dedicated `http.Client` with bounded `MaxIdleConnsPerHost`/`Timeout` rather than `http.DefaultClient`.

### Proof of Concept
Integration test plan:
1. Start a test node with a registered LOOP-p plugin (as in `core/web/loop_registry_internal_test.go`), with the router configured normally (no session/API key).
2. From an unauthenticated HTTP client, fire N (e.g. 50) concurrent `GET /plugins/<name>/debug/pprof/profile?seconds=3600` requests without any `Authorization`/session cookie.
3. Assert: (a) requests succeed in reaching `pluginPPROFHandler` (no 401/403), confirming lack of auth; (b) concurrently issued unrelated authenticated requests (e.g. `GET /v2/build_info`) experience significant added latency or timeout while the pprof requests are in flight, demonstrating worker/connection exhaustion; (c) server resource usage (open goroutines/connections) scales with the attacker-controlled `seconds` value and concurrent request count.

### Citations

**File:** core/web/router.go (L77-91)
```go
	rl := config.WebServer().RateLimit()
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)

	debugRoutes(app, api)
	healthRoutes(app, api)
	sessionRoutes(app, api)
	v2Routes(app, api)
	loopRoutes(app, api)
```

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L185-199)
```go
func metricRoutes(r *gin.RouterGroup) {
	pprofGroup := r.Group("/debug/pprof")
	pprofGroup.GET("/", ginHandlerFromHTTP(pprof.Index))
	pprofGroup.GET("/cmdline", ginHandlerFromHTTP(pprof.Cmdline))
	pprofGroup.GET("/profile", ginHandlerFromHTTP(pprof.Profile))
	pprofGroup.POST("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/symbol", ginHandlerFromHTTP(pprof.Symbol))
	pprofGroup.GET("/trace", ginHandlerFromHTTP(pprof.Trace))
	pprofGroup.GET("/allocs", ginHandlerFromHTTP(pprof.Handler("allocs").ServeHTTP))
	pprofGroup.GET("/block", ginHandlerFromHTTP(pprof.Handler("block").ServeHTTP))
	pprofGroup.GET("/goroutine", ginHandlerFromHTTP(pprof.Handler("goroutine").ServeHTTP))
	pprofGroup.GET("/heap", ginHandlerFromHTTP(pprof.Handler("heap").ServeHTTP))
	pprofGroup.GET("/mutex", ginHandlerFromHTTP(pprof.Handler("mutex").ServeHTTP))
	pprofGroup.GET("/threadcreate", ginHandlerFromHTTP(pprof.Handler("threadcreate").ServeHTTP))
}
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

**File:** core/web/router.go (L445-446)
```go
		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L132-148)
```go
func pprofURLVals(gc *gin.Context) (urlVals url.Values, timeout time.Duration) {
	urlVals = make(url.Values)
	if db, ok := gc.GetQuery("debug"); ok {
		urlVals.Set("debug", db)
	}
	if gc, ok := gc.GetQuery("gc"); ok {
		urlVals.Set("gc", gc)
	}
	timeout = PPROFOverheadSeconds * time.Second
	if sec, ok := gc.GetQuery("seconds"); ok {
		urlVals.Set("seconds", sec)
		if i, err := strconv.Atoi(sec); err == nil {
			timeout = time.Duration(i+PPROFOverheadSeconds) * time.Second
		}
	}
	return
}
```

**File:** core/web/loop_registry.go (L150-166)
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
```

**File:** core/web/loop_registry.go (L190-215)
```go
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
