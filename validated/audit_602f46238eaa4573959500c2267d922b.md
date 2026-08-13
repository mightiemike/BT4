### Title
Unauthenticated `/discovery`, `/plugins/:name/metrics`, and `/plugins/:name/debug/pprof/*` endpoints leak internal LOOP plugin topology and forward pprof/metrics data - (`core/web/router.go`)

### Summary
`loopRoutes` registers `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` directly on the base `api` group with no `auth.Authenticate` middleware, unlike every other sensitive group (`debugRoutes`, `sessionRoutes`, `v2Routes`). Any unauthenticated client that can reach the node's HTTP server can enumerate registered LOOP plugin names/ports via `/discovery` and can also pull live pprof profiles/metrics from internal plugin processes.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the base group `api` (rate-limited + session middleware, but not authenticated) and calls `loopRoutes(app, api)` alongside `debugRoutes`, `healthRoutes`, `sessionRoutes`, and `v2Routes`: [1](#0-0) 

Unlike `debugRoutes` (wrapped in `auth.Authenticate(...)`) and the `authv2` group in `v2Routes`, `loopRoutes` applies no authentication middleware to its routes: [2](#0-1) 

`discoveryHandler` calls `l.registry.List()` (backed by `plugins.LoopRegistry.List()`) and returns a JSON array of Prometheus `targetgroup.Group` entries containing the discovery hostname, exposed port, and `__meta_plugin_name` label for every registered LOOP plugin: [3](#0-2) 

`plugins.LoopRegistry.List()` returns all registered plugin names sorted, with no authorization check performed by the caller: [4](#0-3) 

Beyond information disclosure, the same unauthenticated group also exposes `pluginMetricHandler` and the pprof handlers, which proxy requests to `http://<loopHostName>:<PrometheusPort>/...` on the internal plugin, using only the plugin name path parameter looked up via `registry.Get`: [5](#0-4) [6](#0-5) 

None of these four routes pass through `auth.Authenticate`, `auth.AuthenticateByToken`, or `auth.AuthenticateBySession`, so any network-reachable unauthenticated client can hit them directly.

### Impact Explanation
This is an unauthenticated information disclosure of internal service topology: plugin names, internal hostnames, and Prometheus ports are exposed via `/discovery`, and full pprof profiles / raw Prometheus metrics of internal LOOP plugin processes are retrievable via `/plugins/:name/...`. This does not directly yield fund loss or transaction forgery, but it discloses internal architecture (plugin inventory, ports) useful for targeting further attacks (e.g., probing plugin-specific vulnerabilities, resource exhaustion via repeated pprof `seconds`/`profile` scrapes causing CPU/goroutine profiling load on production plugin processes), and matches an "unauthenticated information disclosure" class finding.

### Likelihood Explanation
Precondition is simply having network access to the node's HTTP server (default listening web port) and at least one registered LOOP plugin (common in OCR2/Mercury/etc. deployments using LOOPP plugins). No credentials, session, or API token are required — the request path bypasses `auth.Authenticate` entirely since `loopRoutes` is called on the un-authenticated `api` group. This is trivially and repeatably exploitable with a single `GET /discovery`.

### Recommendation
Wrap `loopRoutes` registrations in an authenticated group (mirroring `debugRoutes`), e.g.:
```go
func loopRoutes(app chainlink.Application, r *gin.RouterGroup) {
    loopRegistry := NewLoopRegistryServer(app)
    group := r.Group("/", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
    group.GET("/discovery", ginHandlerFromHTTP(loopRegistry.discoveryHandler))
    group.GET("/plugins/:name/metrics", loopRegistry.pluginMetricHandler)
    group.GET("/plugins/:name/debug/pprof/*profile", loopRegistry.pluginPPROFHandler)
    group.POST("/plugins/:name/debug/pprof/symbol", loopRegistry.pluginPPROFPOSTSymbolHandler)
}
```
If `/discovery` must remain reachable by an external, unauthenticated Prometheus scraper by design, gate it behind a separate bearer-token check (similar to `prometheusHandler`'s token check) rather than leaving it fully open, and require authentication for the plugin metrics/pprof proxy endpoints at minimum since they forward to internal-only services.

### Proof of Concept
Integration test plan (Go, using `httptest` against `web.NewRouter`):
1. Build a test `chainlink.Application` mock whose `GetLoopRegistry()` returns a `plugins.LoopRegistry` with one plugin registered via `Register("test-loop")`.
2. Construct the router with `web.NewRouter(app, nil)`.
3. Issue `GET /discovery` with no `Authorization` header and no session cookie.
4. Assert the response status is `200 OK` (not `401`/`403`) and the JSON body contains a `targetgroup.Group` entry with `__meta_plugin_name: "test-loop"` and the configured `exposedPromPort`.
5. Repeat with `GET /plugins/test-loop/metrics` unauthenticated and assert `200 OK` with plugin metrics content instead of `401`.

This demonstrates that `discoveryHandler` and the plugin proxy handlers are reachable without any `auth.Authenticate` check, in contrast to `debugRoutes`/`sessionRoutes`/`v2Routes` which return `401` for the same unauthenticated request pattern.

### Citations

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

**File:** core/web/loop_registry.go (L52-65)
```go
// discoveryHandler implements service discovery of prom endpoints for LOOPs in the registry
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

**File:** core/web/loop_registry.go (L96-128)
```go
func (l *LoopRegistryServer) pluginMetricHandler(gc *gin.Context) {
	pluginName := gc.Param("name")
	p, ok := l.registry.Get(pluginName)
	if !ok {
		gc.Data(http.StatusNotFound, "text/plain", fmt.Appendf(nil, "plugin %q does not exist", html.EscapeString(pluginName)))
		return
	}

	// unlike discovery, this endpoint is internal btw the node and plugin
	pluginURL := fmt.Sprintf("http://%s:%d/metrics", l.loopHostName, p.EnvCfg.PrometheusPort)
	req, err := http.NewRequestWithContext(gc.Request.Context(), "GET", pluginURL, nil)
	if err != nil {
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "error creating plugin metrics request: %s", err))
		return
	}
	res, err := l.promClient.Do(req)
	if err != nil {
		msg := "plugin metric handler failed to get plugin url " + html.EscapeString(pluginURL)
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	if err != nil {
		msg := fmt.Sprintf("error reading plugin %q metrics", html.EscapeString(pluginName))
		l.logger.Errorw(msg, "err", err)
		gc.Data(http.StatusInternalServerError, "text/plain", fmt.Appendf(nil, "%s: %s", msg, err))
		return
	}

	gc.Data(http.StatusOK, "text/plain", b)
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

**File:** plugins/loop_registry.go (L213-226)
```go
// Return slice sorted by plugin name. Safe for concurrent use.
func (m *LoopRegistry) List() []*RegisteredLoop {
	var registeredLoops []*RegisteredLoop
	m.mu.Lock()
	for _, known := range m.registry {
		registeredLoops = append(registeredLoops, known)
	}
	m.mu.Unlock()

	sort.Slice(registeredLoops, func(i, j int) bool {
		return registeredLoops[i].Name < registeredLoops[j].Name
	})
	return registeredLoops
}
```
