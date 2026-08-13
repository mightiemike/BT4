### Title
CORS wildcard origin bypass via suffix collision - ([File: core/services/gateway/network/httpserver.go])

### Summary
The `httpServer.isAllowedOrigin` function strips the `*.` prefix from a configured wildcard entry (e.g. `*.remix.com` → `remix.com`) and then only checks `strings.HasSuffix(originHost, allowedHost)`, without requiring a preceding dot separator. This allows any hostname that merely ends with the same characters (e.g. `notremix.com`, `evilremix.com`) to be treated as a valid subdomain match.

### Finding Description
In `core/services/gateway/network/httpserver.go`, `handleRequest` reads the `Origin` header from an inbound cross-origin request and passes it to `isAllowedOrigin` [1](#0-0) . Inside `isAllowedOrigin`, for wildcard entries the code does:

```go
if strings.HasPrefix(allowedHost, "*.") {
    allowedHost = allowedHost[2:]
    if strings.HasSuffix(originHost, allowedHost) {
        return true
    }
}
``` [2](#0-1) 

Given `CORSAllowedOrigins = ["https://*.remix.com"]`, `allowedHost` becomes `"remix.com"`. For an attacker-controlled origin `https://notremix.com` or `https://evilremix.com`, `originHost` is `"notremix.com"`/`"evilremix.com"`. Both strings literally end with the substring `"remix.com"` (e.g., `"notremix.com"`'s last 9 characters are `r,e,m,i,x,.,c,o,m`), so `strings.HasSuffix` returns `true` even though these are not subdomains of `remix.com` — there is no dot-boundary check (i.e., no verification that the origin equals `allowedHost` or ends with `"."+allowedHost`). As a result `isAllowedOrigin` incorrectly returns `true`, and `handleRequest` echoes the attacker's `Origin` back in `Access-Control-Allow-Origin` [1](#0-0) , enabling the attacker's page to read cross-origin JSON-RPC responses from the gateway.

### Impact Explanation
An unprivileged attacker who registers or controls a suffix-colliding domain (e.g., `evilremix.com`, `notremix.com`) can have their web page's cross-origin requests to the gateway allowed by CORS when the operator intended to only allow true subdomains of `remix.com`. This permits browser-based reading of gateway JSON-RPC responses processed by `HTTPRequestHandler.ProcessRequest` (job/vault/CCIP data returned through this HTTP path) [3](#0-2) , potentially leaking session/credential data or enabling forged privileged requests that rely on browser cookies/session context.

### Likelihood Explanation
Requires `CORSEnabled=true` and at least one wildcard entry in `CORSAllowedOrigins`, both realistic operator configurations. Exploitation only requires the attacker to control (or register) any domain whose name ends with the wildcard's base host string — a low-cost, fully unprivileged, and repeatable attack (no auth, no social engineering).

### Recommendation
Fix `isAllowedOrigin` to require a dot boundary before the suffix match, e.g. check `originHost == allowedHost || strings.HasSuffix(originHost, "."+allowedHost)` instead of a bare `strings.HasSuffix`.

### Proof of Concept
Add a unit test in `core/services/gateway/network/httpserver_test.go`:
```go
s := &httpServer{config: &HTTPServerConfig{CORSAllowedOrigins: []string{"https://*.remix.com"}}, lggr: logger.Test(t)}
assert.False(t, s.isAllowedOrigin("https://notremix.com"))
assert.False(t, s.isAllowedOrigin("https://evilremix.com"))
assert.True(t, s.isAllowedOrigin("https://sub.remix.com"))
```
Expected current (buggy) behavior: the first two assertions fail because `isAllowedOrigin` returns `true`. A fuzz test iterating random suffix-colliding hostnames against configured wildcard patterns can further demonstrate the systemic nature of the bug.

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

**File:** core/services/gateway/network/httpserver.go (L211-222)
```go
	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
	duration := time.Since(startTime)
	s.hMetrics.RecordRequestDuration(r.Context(), httpStatusCode, duration)
	s.hMetrics.RecordRequestCount(r.Context(), httpStatusCode)
```
