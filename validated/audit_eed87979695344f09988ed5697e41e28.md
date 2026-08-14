### Title
Unauthenticated static-asset requests trigger full `fs.WalkDir` over embedded UI tree, enabling CPU/IO exhaustion DoS - ([File: core/web/middleware.go])

### Summary
`EmbedFileSystem.Exists` (core/web/middleware.go:63-76) performs a full `fs.WalkDir` traversal of the entire embedded `assets` file system for every request whose path contains a directory component, only short-circuiting when a matching filename is found. Because `ServeGzippedAssets` (core/web/middleware.go:222-238) calls `Exists` on every incoming request to the UI static-asset route before any authentication check, an unauthenticated attacker can force repeated O(n) filesystem walks by sending GET requests with non-existent nested paths.

### Finding Description
`ServeGzippedAssets` returns a `gin.HandlerFunc` that, for every request, calls `fs.Exists(urlPrefix, c.Request.URL.Path)` (core/web/middleware.go:228) before serving the file or returning 404. `EmbedFileSystem.Exists` only avoids the walk when `path.Base(...)` equals the full trimmed path (i.e., a bare filename with no subdirectory); any path with one or more `/` segments triggers `fs.WalkDir(e.FS, ".", ...)` over the whole embedded `assets` tree (core/web/middleware.go:64-72), scanning every entry until a filename match is found or the tree is exhausted. Since the operator UI's static-asset route is unauthenticated by design (the whole point of serving the UI's HTML/JS/CSS is to work pre-login), an attacker with plain network access to the node's UI/API port can request arbitrarily many non-existent nested paths (e.g. `/a/b/c/nonexistent123`), each of which forces a complete tree walk with no caching, memoization, or rate limiting in this code path. There is no authz, signature, or rate-limit check specific to this handler that would stop it — it runs unconditionally for any request routed to it (e.g., via the SPA fallback / NoRoute handler in `core/web/router.go`).

### Impact Explanation
Repeated concurrent requests with crafted, non-existent nested paths can consume disproportionate CPU/IO on the node's HTTP-serving goroutines relative to a single client request, since each request is O(size of embedded UI asset tree) instead of O(1) map/file lookup. This can degrade or deny availability of the node's UI/API for legitimate users (operators, monitoring, key management flows), matching a "denial of key node functions" impact class.

### Likelihood Explanation
The precondition is simply network access to the node's UI/API port, no authentication or privileged access required, and the exploit is trivially repeatable by any client capable of issuing HTTP GET requests with attacker-chosen paths. However, actual severity depends on the size of the embedded `assets` tree (typically the compiled operator-UI bundle, likely a few hundred to low-thousands of files) and Go's `gin` default worker/connection limits; the walk itself is in-memory (backed by `embed.FS`) rather than real disk I/O, which somewhat bounds worst-case cost per request compared to a real filesystem tree walk.

### Recommendation
Replace the `fs.WalkDir` linear scan with a precomputed index: at startup, build a `map[string]struct{}` (or `map[string][]string]`) of basenames (or full paths) present in the embedded FS once, and have `Exists` perform an O(1) map lookup instead of walking the tree on every request. Alternatively, restrict `Exists`/`Open` to only accept exact, sanitized relative paths (no basename-only fallback matching) so no directory traversal/matching logic is needed per request.

### Proof of Concept
Add a benchmark/fuzz test in `core/web/middleware_test.go`:
1. Instantiate `assetFs := NewEmbedFileSystem(uiEmbedFs, "assets")` (or a synthetic `embed.FS` with N files for a controlled benchmark).
2. Benchmark `assetFs.Exists("/", "/a/b/c/nonexistent-"+randomString())` for many random nonexistent nested paths and measure `b.N` iterations' wall-clock/CPU time, asserting per-request time scales with the number of embedded files (i.e., is not O(1)).
3. Add a concurrency test that fires e.g. 200 concurrent goroutines hitting `ServeGzippedAssets` middleware with random non-existent nested paths and assert that legitimate concurrent requests to `Exists` for valid, existing files are significantly delayed/queued, demonstrating request-queue starvation compared to a baseline map-lookup implementation.