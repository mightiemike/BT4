### Title
Unauthenticated access to LOOP plugin discovery, metrics-proxy, and pprof-proxy endpoints - ([File: core/web/router.go])

### Summary
The `/discovery`, `/plugins/:name/metrics`, `/plugins/:name/debug/pprof/*profile`, and `/plugins/:name/debug/pprof/symbol` routes are registered via `loopRoutes` directly on the top-level `api` route group in `core/web/router.go`, which only carries rate-limiting and session-store middleware, not any `auth.Authenticate(...)` gate. This is in stark contrast to every other diagnostic/admin surface in the same file (`/debug/vars`, `authv2` group's `metricRoutes`), which are explicitly wrapped with `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)`.

### Finding Description
In `core/web/router.go`, `NewRouter` builds the base `api` group with only rate limiting and cookie session middleware attached: [1](#0-0) 
`loopRoutes(app, api)` is called directly on that unauthenticated `api` group, registering: [2](#0-1) 
None of these four handlers are wrapped in `auth.Authenticate(...)`, unlike `debugRoutes` (`/debug/vars`, explicitly gated) or the `metricRoutes` call inside `v2Routes`'s `authv2` group (also gated): [3](#0-2) [4](#0-3) 

The handlers themselves, in `core/web/loop_registry.go`, proxy requests unconditionally to the internal LOOP plugin process's metrics/pprof HTTP server based solely on the `:name` path param, with no caller identity check: `pluginMetricHandler` forwards to `http://<loopHostName>:<PrometheusPort>/metrics`, and `pluginPPROFHandler`/`pluginPPROFPOSTSymbolHandler` forward to `.../debug/pprof/<profile>` including CPU `profile` and `seconds` query params that directly control blocking profiling duration: [5](#0-4) [6](#0-5) 

The existing integration test `TestLoopRegistry` in `core/web/loop_registry_test.go` confirms these routes are callable without any auth header/session — it uses `app.NewHTTPClient(nil)` and successfully receives `200 OK` from `/discovery` and `/plugins/mockLoopImpl/metrics`: [7](#0-6) 

### Impact Explanation
Any unauthenticated network caller reaching the node's web port can enumerate registered LOOP plugins via `/discovery`, scrape internal plugin metrics, and — more seriously — trigger CPU profile collection (`/plugins/:name/debug/pprof/profile?seconds=N`) on the LOOP process hosting OCR/transmission logic. Repeated/parallel CPU-profile requests (bounded by `PPROFOverheadSeconds` plus attacker-controlled `seconds`) impose sustained profiling overhead on the plugin's goroutines, degrading or denying OCR round processing/transmission timeliness — a resource-exhaustion/DoS impact against a node-critical internal component, reachable without any credentials.

### Likelihood Explanation
No preconditions beyond network reachability to the node's configured web server port are required — no session cookie, API token, or admin role is checked on this code path, and the routes are always registered whenever LOOP plugins are used. The attack is trivially repeatable (simple HTTP GET/POST loop) and is already demonstrated to succeed with an unauthenticated client in the existing test suite.

### Recommendation
Wrap `loopRoutes` registration in `auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession)` (and/or token auth) the same way `debugRoutes` and `authv2`'s `metricRoutes` are gated, or move these endpoints into the `authv2`/authenticated group entirely; if external Prometheus scraping without session cookies is required, gate with a dedicated bearer-token check (as already done for `/metrics` via `prometheusHandler`'s token comparison) instead of leaving it fully open.

### Proof of Concept
Integration test plan:
1. Start an `app` with a registered LOOP plugin (as in `TestLoopRegistry`).
2. Using `app.NewHTTPClient(nil)` with no `Authorization` header and no session cookie set, call `GET /discovery`, `GET /plugins/<name>/metrics`, `GET /plugins/<name>/debug/pprof/profile?seconds=1`, and `POST /plugins/<name>/debug/pprof/symbol`.
3. Assert expected behavior should be `401 Unauthorized`/`403 Forbidden`; current behavior (as reproduced by existing `TestLoopRegistry`) returns `200 OK` with proxied plugin data, confirming the missing-auth vulnerability.

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

**File:** core/web/router.go (L180-183)
```go
func debugRoutes(app chainlink.Application, r *gin.RouterGroup) {
	group := r.Group("/debug", auth.Authenticate(app.AuthenticationProvider(), auth.AuthenticateBySession))
	group.GET("/vars", expvar.Handler())
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

**File:** core/web/router.go (L444-446)
```go

		// Debug routes accessible via authentication
		metricRoutes(authv2)
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

**File:** core/web/loop_registry_test.go (L99-140)
```go
	client := app.NewHTTPClient(nil)

	t.Run("discovery endpoint", func(t *testing.T) {
		t.Parallel()
		// under the covers this is routing thru the app into loop registry
		resp, cleanup := client.Get("/discovery")
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("discovery response %s", b)
		var got []*targetgroup.Group
		require.NoError(t, json.Unmarshal(b, &got))

		gotLabels := make([]model.LabelSet, 0, len(got))
		for _, ls := range got {
			gotLabels = append(gotLabels, ls.Labels)
		}
		assert.Len(t, gotLabels, len(expectedLabels))
		for i := range expectedLabels {
			assert.Equal(t, expectedLabels[i], gotLabels[i])
		}
	})

	t.Run("plugin metrics OK", func(t *testing.T) {
		t.Parallel()
		// plugin name `mockLoopImpl` matches key in PluginConfigs
		resp, cleanup := client.Get(expectedLooppEndPoint)
		t.Cleanup(cleanup)
		cltest.AssertServerResponse(t, resp, http.StatusOK)

		b, err := io.ReadAll(resp.Body)
		require.NoError(t, err)
		t.Logf("plugin metrics response %s", b)

		var (
			exceptedCount  = 1
			expectedMetric = fmt.Sprintf("%s %d", testMetricName, exceptedCount)
		)
		require.Contains(t, string(b), expectedMetric)
	})
```
