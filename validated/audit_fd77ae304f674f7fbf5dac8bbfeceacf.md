This confirms the finding. The routes are registered directly on the unauthenticated `api` group without any `auth.Authenticate` middleware wrapping, unlike `sessionRoutes` (`core/web/router.go:216-217`), `debugRoutes` (`core/web/router.go:181`), or the `authv2` group used throughout `v2Routes`.

### Title
Unauthenticated exposure of LOOP plugin discovery, metrics, and pprof profiling/symbol endpoints - ([File: core/web/router.go])

### Summary
`loopRoutes(app, api)` in `core/web/router.go` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the shared `api` group, which only carries rate limiting and session-cookie plumbing but no `auth.Authenticate` middleware. Any network-reachable, unauthenticated attacker can therefore trigger CPU/heap profiling of an internal LOOP process and read its Prometheus metrics without any session or API key.

### Finding Description
In `core/web/router.go`, the top-level `api` group is created with only `rateLimiter` and `sessions.Sessions` middleware, with no authentication attached at that level. `loopRoutes(app, api)` is called on this raw group at line 91, and it registers all four LOOP-related endpoints with no auth middleware wrapper: [1](#0-0) 
Compare this to `debugRoutes`, which wraps `/debug` in an authenticated subgroup: [2](#0-1) 
and `sessionRoutes`, which separates unauthenticated `/sessions` creation from an authenticated group for `DELETE /sessions`: [3](#0-2) 
and the entire `v2Routes` group, which nests almost everything under `authv2 := r.Group("/v2", auth.Authenticate(...))`: [4](#0-3) 
The handlers themselves, `discoveryHandler`, `pluginMetricHandler`, `pluginPPROFHandler`, and `pluginPPROFPOSTSymbolHandler` in `core/web/loop_registry.go`, perform no independent authentication or authorization check; they trust that routing-level middleware has already gated access: [5](#0-4) [6](#0-5) 
`pluginPPROFHandler` forwards the `profile` path parameter and `seconds`/`debug`/`gc` query parameters directly into an outbound request to the internal LOOP process's `/debug/pprof/` endpoint, meaning an attacker fully controls the profiling operation (e.g., `seconds=60` for CPU profiling) with no credential check anywhere in this call chain.

### Impact Explanation
An unauthenticated remote attacker can: (1) enumerate all registered LOOP plugins and their internal metrics endpoints via `/discovery`, (2) pull Prometheus metrics for any named plugin via `/plugins/:name/metrics`, and (3) trigger CPU/heap/goroutine profiling (`/plugins/:name/debug/pprof/profile?seconds=60`, etc.) and symbol resolution (`POST /plugins/:name/debug/pprof/symbol`) against internal LOOP processes. This causes information disclosure of internal operational/runtime metrics and process internals, and enables a resource-exhaustion/DoS vector by forcing repeated long-duration profiling operations against LOOP processes — all without any credentials, in contrast to every comparable internal diagnostic route (`/debug/vars`, `/v2/*` pprof under `metricRoutes(authv2)`) which requires `auth.AuthenticateBySession`.

### Likelihood Explanation
Feasibility is high: the only precondition is that the node's HTTP port is network-reachable and at least one LOOP plugin is registered in `plugins.LoopRegistry`, both very common in production Chainlink node deployments running OCR2/Median/etc. LOOP plugins. No credentials, session, prior privilege, or social engineering is required — a single raw `GET`/`POST` request with no cookies or headers reaches the handler and returns data (not a 401/403), making this trivially repeatable.

### Recommendation
Wrap `loopRoutes` registration in an authenticated group, mirroring `debugRoutes`/`metricRoutes`, e.g. register on `r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))` (or `AuthenticateByToken`/`AuthenticateBySession` as appropriate) instead of the raw `api` group, so `/discovery`, `/plugins/:name/metrics`, and the pprof endpoints all require a valid session/API key before reaching `LoopRegistryServer` handlers.

### Proof of Concept
Integration test in `core/web`: build the router via `NewRouter(app, nil)` with a `LoopRegistry` containing at least one registered plugin (as in `plugins.NewTestLoopRegistry`), start an `httptest.Server`, then issue raw `http.Client{}` requests (no cookie jar, no `Authorization` header) to:
- `GET /discovery`
- `GET /plugins/<name>/metrics`
- `GET /plugins/<name>/debug/pprof/profile?seconds=1`
- `POST /plugins/<name>/debug/pprof/symbol`

Assert that none of these return `401 Unauthorized`; currently they return `200`/`404` (for unknown plugin name) instead of requiring authentication, whereas an equivalent request to `GET /v2/build_info` (behind `authv2`) or `GET /debug/vars` correctly returns `401`.

### Citations

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
}
```

**File:** core/web/router.go (L207-218)
```go
func sessionRoutes(app chainlink.Application, r *gin.RouterGroup) {
	config := app.GetConfig()
	rl := config.WebServer().RateLimit()
	unauth := r.Group("/", rateLimiter(
		rl.UnauthenticatedPeriod(),
		rl.Unauthenticated(),
	))
	sc := NewSessionsController(app)
	unauth.POST("/sessions", sc.Create)
	auth := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	auth.DELETE("/sessions", sc.Destroy)
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

**File:** core/web/router.go (L245-248)
```go
	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
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
