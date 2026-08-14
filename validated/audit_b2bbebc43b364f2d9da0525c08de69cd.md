### Title
Unauthenticated per-request full `fs.WalkDir` traversal in `EmbedFileSystem.Exists` enables CPU-exhaustion DoS - ([File: core/web/middleware.go])

### Summary
`EmbedFileSystem.Exists` is invoked on every incoming HTTP request to the operator UI route before any authentication is checked, and for any path that doesn't exactly match `prefix` it performs a full `fs.WalkDir` over the entire embedded `assets` filesystem. An attacker can send many concurrent requests with unique non-matching paths to force repeated, unbounded, request-independent-cost traversals, consuming CPU on the node's HTTP server.

### Finding Description
`ServeGzippedAssets` registers a gin middleware that calls `fs.Exists(urlPrefix, c.Request.URL.Path)` for every request before serving static files [1](#0-0) . `EmbedFileSystem.Exists` strips the prefix from the request path and, whenever the resulting basename differs from the raw path (i.e. essentially any path containing a `/`, which is the normal case for asset paths like `/assets/js/main.js`), performs `fs.WalkDir(e.FS, ".", ...)` over the entire embedded `assets` tree, comparing `path.Base(fpath)` against the target basename for every single entry until a match or exhaustion [2](#0-1) . This means the cost of `Exists` is O(N) in the number of embedded files for essentially every request, not O(1) or O(log N), and this happens unconditionally regardless of whether the path is valid, and this handler runs on the public UI route without prior session/auth gating (per `router.go` wiring of `ServeGzippedAssets`).

An attacker with only network access to the operator UI listener can send a large volume of concurrent GET requests with distinct nonexistent, deeply-pathed filenames (e.g. `/assets/aaaaaaaa1`, `/assets/aaaaaaaa2`, ...), each of which will never find `found=true` early and will therefore walk the complete embedded tree to conclusion. Because the number of concurrent, cheap-to-generate requests is attacker-controlled and unbounded (no rate limiting is applied ahead of this handler for static asset requests), the aggregate CPU cost scales with `attacker requests × embedded file count`, which is a classic algorithmic-complexity DoS amplification.

### Impact Explanation
This is a CPU-exhaustion / availability issue on the node's HTTP server goroutines. Since the Chainlink node runs the HTTP API/UI server in the same process as the job scheduler, OCR round processing, and key-signing logic, sustained CPU saturation from this handler can starve other goroutines competing for the same OS threads/GOMAXPROCS, plausibly delaying time-sensitive OCR heartbeat/report-signing operations. The impact is scoped to availability/DoS with a possible secondary timeliness degradation on signing-related subsystems, rather than any direct confidentiality or fund-loss impact.

### Likelihood Explanation
No authentication or authorization is required — only network reachability to the operator UI HTTP listener, matching the "unprivileged attacker" precondition. The attack is trivially repeatable and requires only cheap, distinct HTTP GET requests, which are easy to generate in high volume from a single machine or a small botnet. The severity scales with the number of files embedded under `assets` (the compiled operator UI bundle), which is typically not tiny (JS/CSS/font/image chunks), making each walk non-trivial.

### Recommendation
Replace the per-request `fs.WalkDir` full traversal with an O(1) or O(log N) lookup: build an index (e.g., a `map[string]struct{}` or a trie of valid asset paths) once at startup when `NewEmbedFileSystem` is constructed, and have `Exists` do a direct map lookup instead of walking the tree per request. Alternatively, use `fs.Stat`/`fs.ReadDir` on the exact resolved path (since `embed.FS` supports direct lookups) instead of basename-matching via a full walk. Additionally, consider applying a rate limiter ahead of the static-asset route.

### Proof of Concept
Add a benchmark/fuzz test in `core/web/middleware_test.go` (or a new file) that:
1. Constructs an `EmbedFileSystem` backed by a synthetic `embed.FS`-like test double or the real `assetFs` with a large number of files (e.g., generate an embed tree with 10,000 nested files).
2. Measures wall-clock time of `Exists("/", uniqueNonMatchingPath)` for a single call, then measures total wall-clock time for calling `Exists` concurrently N times with N goroutines and unique paths.
3. Assert that time-per-call does not grow linearly with the number of embedded files (i.e., compare `Exists` cost between a filesystem with 100 files vs. 10,000 files and assert the ratio is roughly constant/O(1), not proportional to the 100x file-count increase).
4. Assert that concurrent calls scale reasonably (e.g., total CPU-seconds consumed is bounded and does not explode with concurrency × tree size), demonstrating the current implementation fails this invariant while a map-based fix passes it.

### Citations

**File:** core/web/middleware.go (L62-76)
```go
// Exists implements the ServeFileSystem interface.
func (e *EmbedFileSystem) Exists(prefix string, filepath string) (found bool, err error) {
	if p := path.Base(strings.TrimPrefix(filepath, prefix)); len(p) < len(filepath) {
		err = fs.WalkDir(e.FS, ".", func(fpath string, d fs.DirEntry, err error) error {
			fileName := path.Base(fpath)
			if fileName == p {
				found = true
				return fs.SkipAll
			}
			return nil
		})
	}

	return
}
```

**File:** core/web/middleware.go (L221-237)
```go
// ServeGzippedAssets returns a middleware handler that serves static files in the given directory.
func ServeGzippedAssets(urlPrefix string, fs ServeFileSystem, lggr logger.Logger) gin.HandlerFunc {
	fileserver := GzipFileServer(fs, lggr)
	if urlPrefix != "" {
		fileserver = http.StripPrefix(urlPrefix, fileserver)
	}
	return func(c *gin.Context) {
		if ok, err := fs.Exists(urlPrefix, c.Request.URL.Path); err != nil {
			lggr.Errorw("Failed to search for file", "err", err)
			c.AbortWithStatus(http.StatusInternalServerError)
		} else if ok {
			fileserver.ServeHTTP(c.Writer, c.Request)
			c.Abort()
		} else {
			c.AbortWithStatus(http.StatusNotFound)
		}
	}
```
