### Title
CORS wildcard suffix match bypass allows non-subdomain origins to be reflected as allowed - ([File: core/services/gateway/network/httpserver.go])

### Summary
The `isAllowedOrigin` function strips the `*.` prefix from a configured wildcard origin (e.g. `*.remix.com` → `remix.com`) and then checks `strings.HasSuffix(originHost, allowedHost)` with no boundary check for a preceding dot. This allows a hostname like `evilremix.com` to match the wildcard `*.remix.com` even though it is not a subdomain, causing the gateway to reflect `Access-Control-Allow-Origin` for an attacker-controlled origin.

### Finding Description
In `httpServer.isAllowedOrigin` [1](#0-0) , when an allowed origin has the `*.` prefix, the code does:
```go
allowedHost = allowedHost[2:]
if strings.HasSuffix(originHost, allowedHost) {
    return true
}
```
For `allowed = "*.remix.com"`, `allowedHost` becomes `"remix.com"`. `strings.HasSuffix("evilremix.com", "remix.com")` evaluates to `true` because `"evilremix.com"` literally ends with the character sequence `"remix.com"` — there is no check that the character immediately preceding the suffix in `originHost` is a `.` (i.e., no requirement that `originHost` be `allowedHost` or end with `"."+allowedHost`).

This is reachable directly from `handleRequest` [2](#0-1) , which reads the client-supplied `Origin` header and calls `isAllowedOrigin(origin)`; if true, it sets `Access-Control-Allow-Origin` to the raw attacker-supplied origin value. No authentication, signature, or rate-limit check precedes this — it is exercised on every request when `CORSEnabled=true`, including simple `GET`/`POST`/`OPTIONS` requests from any unprivileged web client.

The existing unit tests only cover true subdomains and unrelated domains that don't share a bare-suffix collision (e.g., `ethereum.remix.org` doesn't end with `ethereum.org`), so this boundary-check gap is not caught by current tests [3](#0-2) .

### Impact Explanation
If an operator configures a wildcard CORS origin such as `*.remix.com` intending to allow only genuine subdomains, an attacker who registers or controls a domain such as `evilremix.com` (or any domain textually ending in the configured suffix without a dot boundary, e.g. `notremix.com`) can have their origin reflected in `Access-Control-Allow-Origin`. A browser running attacker-controlled JavaScript on that origin can then make authenticated cross-origin requests to the gateway HTTP endpoint (including with credentials, if cookies/auth headers are used), enabling session/credential theft or unauthorized calls against the gateway's `ProcessRequest` handler. This matches "cross-origin session/credential theft against gateway HTTP endpoint."

### Likelihood Explanation
Requires `CORSEnabled=true` and a wildcard entry in `CORSAllowedOrigins` (an operator-controlled but plausible/common configuration pattern, e.g. `*.remix.com` for the Remix IDE). Given that precondition, exploitation requires no privilege at all — any attacker can register a domain with the matching suffix (no dot boundary) and send a normal HTTP request with a spoofed `Origin` header; this is fully repeatable and deterministic.

### Recommendation
Fix the suffix check to require a proper subdomain boundary, e.g.:
```go
if strings.HasPrefix(allowedHost, "*.") {
    base := allowedHost[2:]
    if originHost == base || strings.HasSuffix(originHost, "."+base) {
        return true
    }
}
```
This ensures `evilremix.com` does not match `*.remix.com`, while `x.remix.com` still correctly matches.

### Proof of Concept
Add a unit test for `isAllowedOrigin` (or extend `httpserver_test.go`) with `CORSAllowedOrigins = []string{"https://*.remix.com"}`:
- `origin = "https://evilremix.com"` → expect `isAllowedOrigin` to return `false` (currently returns `true`).
- `origin = "https://notremix.com"` → expect `false` (currently returns `true`).
- `origin = "https://x.remix.com.evil.com"` → expect `false` (verify current behavior separately, since suffix here is `evil.com`, not `remix.com`, so this case likely already fails correctly, but should be asserted).
- `origin = "https://sub.remix.com"` → expect `true` (true subdomain, should remain allowed).

Run via the existing HTTP integration harness `TestHTTPServer_HandleRequest_CORSEnabled_FromNotAllowedOriginWildcards`-style test [4](#0-3) , sending a request with `Origin: https://evilremix.com` against a server configured with `CORSAllowedOrigins: []string{"https://*.remix.com"}`, and assert that `Access-Control-Allow-Origin` header is empty (currently it will be reflected as `https://evilremix.com`, demonstrating the bug).

### Citations

**File:** core/services/gateway/network/httpserver.go (L169-175)
```go
		// check for wildcard host match (e.g., *.remix.com)
		if strings.HasPrefix(allowedHost, "*.") {
			allowedHost = allowedHost[2:]
			if strings.HasSuffix(originHost, allowedHost) {
				return true
			}
		}
```

**File:** core/services/gateway/network/httpserver.go (L180-187)
```go
func (s *httpServer) handleRequest(w http.ResponseWriter, r *http.Request) {
	if s.config.CORSEnabled {
		origin := r.Header.Get("Origin")
		if s.isAllowedOrigin(origin) {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		}
```

**File:** core/services/gateway/network/httpserver_test.go (L115-149)
```go
func TestHTTPServer_HandleRequest_CORSEnabled_FromAllowedOriginWildcards(t *testing.T) {
	t.Parallel()
	_, handler, url := startNewServer(t, 100_000, 100_000, true,
		[]string{"https://*.ethereum.org", "https://*.valid.domain.com", "http://*.gov"})

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin := "https://remix.ethereum.org"
	resp, respBytes := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Equal(t, origin, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Equal(t, "GET, POST, OPTIONS", resp.Header.Get("Access-Control-Allow-Methods"))
	require.Equal(t, "Content-Type", resp.Header.Get("Access-Control-Allow-Headers"))

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin = "https://another.valid.domain.com"
	resp, respBytes = sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Equal(t, origin, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Equal(t, "GET, POST, OPTIONS", resp.Header.Get("Access-Control-Allow-Methods"))
	require.Equal(t, "Content-Type", resp.Header.Get("Access-Control-Allow-Headers"))

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin = "http://example.gov"
	resp, respBytes = sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Equal(t, origin, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Equal(t, "GET, POST, OPTIONS", resp.Header.Get("Access-Control-Allow-Methods"))
	require.Equal(t, "Content-Type", resp.Header.Get("Access-Control-Allow-Headers"))
}
```

**File:** core/services/gateway/network/httpserver_test.go (L181-215)
```go
func TestHTTPServer_HandleRequest_CORSEnabled_FromNotAllowedOriginWildcards(t *testing.T) {
	t.Parallel()
	_, handler, url := startNewServer(t, 100_000, 100_000, true,
		[]string{"https://*.ethereum.org", "https://*.valid.domain.com", "http://example.gov:8080"})

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin := "https://ethereum.remix.org" // doesn't end with ethereum.org
	resp, respBytes := sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Methods"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Headers"))

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin = "http://another.valid.domain.org" // http instead of https
	resp, respBytes = sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Methods"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Headers"))

	handler.On("ProcessRequest", mock.Anything, mock.Anything, mock.Anything).Return([]byte("response"), 200)

	origin = "http://example.gov" // port missing
	resp, respBytes = sendRequest(t, url, []byte("0123456789"), http.MethodPost, &origin)
	require.Equal(t, http.StatusOK, resp.StatusCode)
	require.Equal(t, []byte("response"), respBytes)
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Origin"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Methods"))
	require.Empty(t, resp.Header.Get("Access-Control-Allow-Headers"))
}
```
