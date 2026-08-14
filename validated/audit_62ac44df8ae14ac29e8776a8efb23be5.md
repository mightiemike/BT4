### Title
Unauthenticated attacker can force the node to hold long-lived internal pprof-forwarding connections via unbounded `seconds` parameter, causing resource exhaustion - ([File: core/web/loop_registry.go])

### Summary
The LOOP plugin pprof-forwarding routes (`GET /plugins/:name/debug/pprof/*profile` and `POST /plugins/:name/debug/pprof/symbol`) are registered on the bare, unauthenticated `api` router group, unlike every other sensitive endpoint (`metricRoutes`, `debugRoutes`, `authv2`) which requires `auth.Authenticate`. An unprivileged attacker can therefore enumerate plugin names via the also-unauthenticated `/discovery` endpoint and then POST arbitrary body content plus `debug`, `gc`, and `seconds` query parameters to `/plugins/:name/debug/pprof/symbol`, which are forwarded verbatim to the LOOP plugin's internal pprof server. The `seconds` value is parsed with `strconv.Atoi` and used unbounded to compute the outgoing request's context timeout, letting the attacker force the node's `http.DefaultClient` to hold a connection/goroutine open for an attacker-chosen duration.

### Finding Description
`loopRoutes` is wired directly onto the top-level `api` group with no authentication middleware: [1](#0-0) 

Compare this to the pprof endpoints under `metricRoutes`, which are deliberately placed behind full session/token authentication inside `authv2`: [2](#0-1) 

The plugin pprof forwarding handler reads the raw request body and any `debug`/`gc`/`seconds` query parameters and forwards them to the plugin's internal `/debug/pprof/symbol` endpoint: [3](#0-2) 

`pprofURLVals` computes an unbounded timeout directly from the attacker-supplied `seconds` value with no upper clamp, minimum, or sanity check: [4](#0-3) 

`doRequest` then issues the forwarded request using `http.DefaultClient` bound only by that attacker-influenced timeout: [5](#0-4) 

Because `debug/pprof/profile` and `debug/pprof/symbol` on the real Go pprof handler honor the `seconds` parameter by sleeping/profiling for that duration, an attacker who supplies a very large `seconds` value forces the downstream plugin pprof handler (and the node's forwarding request/goroutine/connection) to remain open for that entire time. There is no cap on `seconds`, no rate limiting specific to this route beyond the generic authenticated-rate-limiter (which does not even apply here since there's no session), and no authentication gate at all on these routes. An attacker can first call the also-unauthenticated discovery endpoint to learn valid plugin names: [6](#0-5) 

### Impact Explanation
This is an unauthenticated-access issue compounded by a resource-exhaustion vector: any external, unprivileged party reaching the node's HTTP interface can (1) enumerate registered LOOP plugins, (2) forward attacker-controlled bodies/query parameters into an internal-only pprof interface that was never intended to be externally reachable, and (3) hold node goroutines/connections open for attacker-chosen durations by inflating `seconds`, degrading node availability under concurrent abuse (repeated requests exhaust the default HTTP client's connection pool / node file descriptors / goroutines). This matches a node availability/DoS impact and an authorization-bypass impact (sensitive debug/profiling surface reachable without any credentials), which are broken invariants relative to every other administrative endpoint in `router.go`.

### Likelihood Explanation
High feasibility: no privileges, keys, or social engineering are required — only network access to the node's web server. The routes are registered unconditionally in `loopRoutes` whenever any LOOP plugin is running, which is standard in modern Chainlink node deployments (Median, OCR2 plugins, etc.). The attack is trivially repeatable and scriptable (simple HTTP requests).

### Recommendation
- Require authentication (`auth.Authenticate`) on `loopRoutes`, consistent with `metricRoutes` and other debug/admin routes, so only authenticated operators can access plugin pprof/metrics forwarding.
- Clamp `seconds` (and any duration-like parameter parsed from `pprofURLVals`) to a strict maximum (e.g., a few seconds beyond `PPROFOverheadSeconds`), and reject negative or absurdly large values before using them to build the outgoing context timeout.
- Consider using a dedicated `http.Client` with `MaxIdleConnsPerHost`/overall concurrency caps for these forwarding calls instead of `http.DefaultClient`, to bound resource usage regardless of parameter values.

### Proof of Concept
Unit/fuzz test plan for `core/web/loop_registry.go`:
1. Unit test `TestPprofURLVals_SecondsBounds`: call `pprofURLVals` with a `gin.Context` whose query contains `seconds=999999999` (and separately a negative value, and a non-numeric value). Assert that the returned `timeout` is clamped to a fixed maximum (e.g., ≤ `PPROFOverheadSeconds + MaxAllowedSeconds`) rather than growing unbounded with the input.
2. Integration test `TestLoopRoutes_Unauthenticated`: spin up `web.NewRouter` with a registered dummy plugin (mocking `p.EnvCfg.PrometheusPort` to point at a local `httptest.Server` that simulates pprof's `seconds`-based delay), then issue `POST /plugins/:name/debug/pprof/symbol?seconds=<large>` without any session cookie or API token. Assert the request is rejected with 401/403 once auth middleware is added, and that before the fix, the request succeeds and blocks for the full attacker-supplied duration, confirming both the auth gap and the unbounded timeout.
3. Fuzz test over `seconds` values (negative, `math.MaxInt64`, non-numeric strings) feeding `pprofURLVals`, asserting `doRequest`'s derived `context.WithTimeout` never exceeds the clamp and `doRequest` returns within a bounded test deadline.

### Citations

**File:** core/web/router.go (L78-91)
```go
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

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
```

**File:** core/web/loop_registry.go (L53-65)
```go
func (l *LoopRegistryServer) discoveryHandler(w http.ResponseWriter, req *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	groups := make([]*targetgroup.Group, 0, 1+len(l.registry.List()))

	// add node metrics to service discovery
	groups = append(groups, pluginGroup(l.discoveryHostName, l.exposedPromPort, "/metrics"))

	// add all the plugins
	for _, registeredPlugin := range l.registry.List() {
		group := pluginGroup(l.discoveryHostName, l.exposedPromPort, pluginMetricPath(registeredPlugin.Name))
		group.Labels[LabelMetaPluginName] = model.LabelValue(registeredPlugin.Name)
		groups = append(groups, group)
	}
```

**File:** core/web/loop_registry.go (L130-148)
```go
const PPROFOverheadSeconds = 30

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

**File:** core/web/loop_registry.go (L168-188)
```go
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
```

**File:** core/web/loop_registry.go (L190-205)
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
```
