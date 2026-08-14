### Title
Timing-unsafe token comparison on Prometheus metrics endpoint enables password recovery via timing attack - (File: core/web/router.go)

### Summary
The `prometheusHandler` function in `core/web/router.go` compares the `Authorization` header against the expected `Bearer <token>` value using Go's native `!=` string comparison, which is not constant-time. This mirrors the exact bug class from the external report: a non-constant-time equality check on a secret token allows an unauthenticated network attacker to recover the Prometheus `AuthToken` character-by-character via a timing side channel.

### Finding Description
In `core/web/router.go`, the Prometheus metrics endpoint is protected by a bearer-token check: [1](#0-0) 

```go
func prometheusHandler(token string, h http.Handler) gin.HandlerFunc {
	return func(c *gin.Context) {
		if token == "" {
			h.ServeHTTP(c.Writer, c.Request)
			return
		}

		header := c.Request.Header.Get("Authorization")

		if header == "" {
			c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
			return
		}

		bearer := "Bearer " + token

		if header != bearer {
			c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
			return
		}

		h.ServeHTTP(c.Writer, c.Request)
	}
}
```

`header != bearer` uses Go's built-in string comparison, which — like V8's `String::Equals`/`StringComparator` described in the external report — is not constant time: it returns as soon as it finds a length or byte mismatch. Go's runtime string comparison (`runtime.cmpstring`) compares byte-by-byte and returns early on the first differing byte, exactly the same class of leak documented in the report for V8's `StringComparator::Equals`. An attacker who can measure response timing (e.g., colocated on the same host/network, or via a large number of samples to average out jitter) can:
1. Determine when their guessed length differs from the true `bearer` string.
2. Recover the token one character at a time by observing which guessed prefix causes the comparison to run marginally longer before failing.

Unlike the properly hardened comparisons elsewhere in this codebase — e.g., `AuthenticateBridgeType` and `AuthenticateExternalInitiator` in `core/bridges/bridge_type.go`/`external_initiator.go`, `AuthenticateUserByToken` in `core/sessions/session.go`, and `constantTimeEmailCompare` in `core/sessions/localauth/orm.go`, all of which correctly use `crypto/subtle.ConstantTimeCompare` — the `prometheusHandler` in `core/web/router.go` uses a naive `!=` comparison for a secret credential.

This endpoint is reachable by an unauthenticated network client. It is not gated behind session/API-token authentication like the rest of the JSON-API routes; it is a separate, standalone bearer-token gate configured via `Prometheus.AuthToken` as documented in `docs/SECRETS.md`: [2](#0-1) 

### Impact Explanation
The Prometheus `AuthToken` is a Chainlink node secret intended to protect the `/metrics` endpoint, which exposes internal operational and performance data about the node (job execution stats, queue depths, database/RPC metrics, potentially chain/account-related gauges). Recovering this token via timing attack allows an unauthenticated attacker to:
- Gain unauthorized access to the node's internal Prometheus metrics, which is a secret-disclosure trust-boundary violation (the config comment explicitly labels `Prometheus.AuthToken` a secret in `docs/SECRETS.md`).
- Use exposed metrics for further reconnaissance/targeting of the node (e.g., identifying job/queue behavior, error rates, potentially chain-specific counters) that could support subsequent attacks.

This does not directly grant node control, private key access, or job manipulation, but it is a concrete authentication-bypass of a secret-token-gated endpoint, satisfying the "secret disclosure" trust-boundary criterion.

### Likelihood Explanation
Exploitation requires the classic timing-attack conditions from the report: many timed requests (thousands per candidate character) and a way to obtain accurate timing measurements (e.g., attacker on the same host/VM/network segment, or averaging over large sample counts to filter noise across the network). This is a non-trivial but well-documented and previously-demonstrated attack class (as shown in the external report's PoC against a structurally identical `===`/`!=` password check). The vulnerable code path is trivially reachable by any unauthenticated actor who can send HTTP requests to the metrics port, requiring no prior credentials.

### Recommendation
Replace the direct string comparison with a constant-time comparison, consistent with the rest of the codebase's pattern (e.g., `crypto/subtle.ConstantTimeCompare`):

```go
import "crypto/subtle"

bearer := "Bearer " + token
if subtle.ConstantTimeCompare([]byte(header), []byte(bearer)) != 1 {
    c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
    return
}
```
Additionally, pad/normalize lengths (as done in `constantTimeEmailCompare`) or hash both values with a fixed-length MAC before comparing, to avoid leaking length information via early-return-on-length-mismatch behavior of `ConstantTimeCompare` itself (it returns 0 immediately, without timing leak, if lengths differ — but doing a fixed-size HMAC compare is the most robust choice for header/token comparisons of variable length).

### Proof of Concept
Conceptually identical to the report's PoC against the JSON-RPC `authenticate` route: an attacker sends repeated GET requests to the Prometheus metrics endpoint with the `Authorization: Bearer <guess>` header, incrementally brute-forcing the `AuthToken` character-by-character while measuring response latency for the `header != bearer` branch in `prometheusHandler` (`core/web/router.go`). Because the underlying Go string comparison exits on the first mismatched byte (same class of behavior as V8's `StringComparator::Equals` in the original report), correct-prefix guesses take measurably longer than incorrect ones, allowing full token recovery given sufficient timing samples. No working exploit was executed against the live node in this analysis; the finding is based on static code review confirming the non-constant-time comparison and its exposure to unauthenticated requests.

### Citations

**File:** core/web/router.go (L676-699)
```go
// use is adapted from ginprom.prometheusHandler to add support for custom http.Handler
func prometheusHandler(token string, h http.Handler) gin.HandlerFunc {
	return func(c *gin.Context) {
		if token == "" {
			h.ServeHTTP(c.Writer, c.Request)
			return
		}

		header := c.Request.Header.Get("Authorization")

		if header == "" {
			c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
			return
		}

		bearer := "Bearer " + token

		if header != bearer {
			c.String(http.StatusUnauthorized, ginprom.ErrInvalidToken.Error())
			return
		}

		h.ServeHTTP(c.Writer, c.Request)
	}
```

**File:** docs/SECRETS.md (L133-146)
```markdown
## Prometheus
```toml
[Prometheus]
AuthToken = "prometheus-token" # Example
```


### AuthToken
```toml
AuthToken = "prometheus-token" # Example
```
AuthToken is the authorization key for the Prometheus metrics endpoint.

Environment variable: `CL_PROMETHEUS_AUTH_TOKEN`
```
