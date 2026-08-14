### Title
Unauthenticated CPU-exhaustion DoS via unbounded `fs.WalkDir` traversal in `EmbedFileSystem.Exists` - ([File: core/web/middleware.go])

### Summary
`EmbedFileSystem.Exists` performs a full `fs.WalkDir` traversal of the entire embedded operator-UI asset tree on every request whose requested filename is not found by a simple prefix check, and this method is invoked on every incoming HTTP request to the static-asset route via `ServeGzippedAssets` with no authentication required. An attacker can send many concurrent requests with unique, non-matching paths, forcing repeated full-tree walks and consuming CPU cycles in the same process that runs job scheduling and OCR/signing routines.

### Finding Description
`ServeGzippedAssets` calls `fs.Exists(urlPrefix, c.Request.URL.Path)` for every request before deciding whether to serve the file or return 404 [1](#0-0) . `EmbedFileSystem.Exists` walks the entire embedded `assets` filesystem with `fs.WalkDir(e.FS, ".", ...)`, comparing `path.Base(fpath)` against the requested base name for every file in the tree until a match is found or the walk completes [2](#0-1) . Additionally, `findBestFile` in the gzip file handler calls `Exists` a second time (for the `.gz` variant) per request when an `Accept-Encoding` header is present [3](#0-2) , doubling the walk cost per request in the common case. Because the walk only terminates early via `fs.SkipAll` on a match, any request for a non-existent asset (which is the worst case, and fully attacker-controlled by choosing an arbitrary path) forces a full O(N) traversal of the compiled UI's file tree, where N is the number of embedded files/directories. This route (serving the operator UI's static frontend) is reachable pre-authentication because the browser must be able to fetch the login page's JS/CSS/HTML assets before a session exists, meaning there is no rate limiting, session check, or CSRF gate in front of this handler in `middleware.go`/`router.go`.

### Impact Explanation
This is a real algorithmic-complexity inefficiency: cost per request scales with the size of the embedded asset tree rather than being O(1)/O(log N), and it is fully triggerable by an unauthenticated client picking arbitrary nonexistent paths, with no caching of results. Under concurrent load this can consume significant CPU on the node process. However, the embedded filesystem here is a fixed, build-time-bounded operator UI asset bundle (a few hundred to a few thousand static files at most) — it is not attacker-expandable, so the walk cost, while linear per request, has a fixed and known upper bound (not unbounded amplification). The claimed chaining into "blockchain-key-signing timeliness" (OCR heartbeats, job scheduler) depends on this HTTP-serving code sharing the same OS process/goroutine scheduler as signing-critical goroutines, which is true for the default single-binary Chainlink node deployment, but Go's cooperative goroutine scheduler with GOMAXPROCS typically isolates this kind of degradation to general request latency rather than guaranteeing starvation of unrelated goroutines (signing operations are not blocked by CPU-bound loops in another goroutine unless the whole system is saturated). This is a legitimate performance/DoS-class finding but the impact should be characterized as a general availability/resource-consumption issue on the operator UI HTTP listener, not a proven direct compromise of key-signing correctness or confidentiality.

### Likelihood Explanation
Feasible and repeatable with no privileges: any client with network access to the operator UI HTTP listener can send a high volume of concurrent GET requests for arbitrary non-existent paths, each of which forces at least one (often two, due to the gzip variant check) full `fs.WalkDir` traversal [4](#0-3) . No authentication, CSRF token, or per-IP rate limiting is present in this code path.

### Recommendation
Replace the per-request `fs.WalkDir` traversal with an O(1) lookup: precompute a set (map) of all file basenames (or full paths) in the embedded `assets` tree once at startup (e.g., in `NewEmbedFileSystem`), and have `Exists` perform a map lookup instead of walking the tree on every call. This removes the request-time cost scaling with asset-tree size entirely.

### Proof of Concept
Add a benchmark in `core/web/middleware_test.go` (or a new `middleware_bench_test.go`) that:
1. Constructs an `EmbedFileSystem` over the real `assets` embed.FS (or a synthetic `embed.FS`-like `fstest.MapFS` with N files, parameterized as N grows: 100, 1,000, 10,000).
2. Calls `Exists(prefix, "/nonexistent-file-xyz")` in a tight loop / via `testing.B` with `b.RunParallel`, measuring `ns/op`.
3. Asserts that `ns/op` grows roughly linearly with N (proving the current O(N) cost), demonstrating the fix should keep `ns/op` flat as N increases after switching to a map-based lookup.
4. An integration-style test can spin up `ServeGzippedAssets` behind an `httptest.Server`, fire `runtime.NumCPU()*50` concurrent requests for random nonexistent paths, and measure aggregate wall-clock/CPU time before vs. after the fix.

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

**File:** core/web/middleware.go (L140-150)
```go
	var available []string
	for _, posenc := range preferredEncodings {
		ext := extensionForEncoding(posenc)
		fname := fpath + ext

		if ok, err := f.root.Exists("/", fname); err != nil {
			return nil, nil, err
		} else if ok {
			available = append(available, posenc)
		}
	}
```

**File:** core/web/middleware.go (L227-237)
```go
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
