### Title
Per-sender rate limiter bypass via inconsistent address normalization between allowlist check and rate limiter key - ([File: core/services/functions/connector_handler.go])

### Summary
In `functionsConnectorHandler.HandleGatewayMessage`, the allowlist check normalizes the sender via `ethCommon.HexToAddress(body.Sender)` before authorization, but the rate limiter is keyed on the raw, un-normalized `body.Sender` string. An attacker who controls the exact hex-string formatting of `Sender` in the gateway message (e.g., varying case or capitalization) can produce many distinct rate-limiter keys that all resolve to the same allowlisted on-chain address, defeating per-sender throttling.

### Finding Description
The handler is:
```go
fromAddr := ethCommon.HexToAddress(body.Sender)
if !h.allowlist.Allow(fromAddr) { ... return nil }
if !h.rateLimiter.Allow(body.Sender) { ... return nil }
``` [1](#0-0) 

`ethCommon.HexToAddress` parses the hex string case-insensitively into a canonical 20-byte `common.Address`, so `"0xAbCd..."`, `"0xabcd..."`, and other casing variants all map to the same `fromAddr` and thus pass `h.allowlist.Allow(fromAddr)` identically (the allowlist's internal map is keyed by `common.Address`, confirmed in `onchainAllowlist.Allow`) [2](#0-1) . However, `h.rateLimiter.Allow(body.Sender)` is called with the raw, un-normalized string, so distinct casings of the same address are treated as distinct rate-limiter keys.

`body.Sender` originates from `msg.Body` decoded from `hc.ValidatedMessageFromReq(req)`, which is attacker-supplied gateway message content [3](#0-2) . Because message signature/authorization is address-based (via `fromAddr`), an attacker with a single allowlisted key can craft the JSON message's `Sender` string with varying hex-case combinations for every request while keeping the underlying address (and thus signature) identical, causing each request to hit a fresh, never-before-seen key in `h.rateLimiter`'s internal per-sender bucket store.

This affects request paths for `MethodSecretsSet` (S4 storage writes) and `MethodHeartbeat` (offchain request dispatch/goroutine spawning), both gated by this same rate limiter check before dispatch.

### Impact Explanation
An allowlisted-but-otherwise-unprivileged sender can flood the node with unlimited `MethodSecretsSet` or `MethodHeartbeat` requests despite the intended per-sender rate limit, because each casing variant of their own address string opens a new bucket in the underlying rate limiter's per-sender map. This can lead to storage abuse in S4 (`h.storage.Put`) and excessive goroutine/resource consumption in `handleHeartbeat`/`handleOffchainRequest`, i.e., resource exhaustion — matching a "Denial of Service / resource abuse via bypass of security control" class of impact.

### Likelihood Explanation
Feasibility is high for an attacker who is already allowlisted (a legitimate but rate-limited functions/DON user): they only need to alter string casing of the `Sender` field in the JSON-RPC gateway message body per request; the signature/authorization is verified against the normalized `fromAddr`, not the raw string, so this manipulation does not break message authenticity. No node-operator privilege or key leakage is required beyond what's needed to be an allowlisted requester in the first place. This is easily automated to generate an effectively unbounded number of unique casing permutations for any given address.

### Recommendation
Key the rate limiter on the normalized address instead of the raw string, e.g., call `h.rateLimiter.Allow(fromAddr.Hex())` (or `strings.ToLower(fromAddr.Hex())`) using the already-normalized `fromAddr` derived from `ethCommon.HexToAddress(body.Sender)`, ensuring the rate-limiting identity always matches the authorization identity.

### Proof of Concept
Unit test in `core/services/functions/connector_handler_test.go`:
1. Configure `h.rateLimiter` (or a stub with a small per-sender burst, e.g., burst=1, RPS=very low) and an allowlist containing address `0xAbC...123`.
2. Send `N` `HandleGatewayMessage` calls (N > configured burst) where each call uses the same underlying address but a different string casing/format for `body.Sender` (e.g., all-lowercase, all-uppercase, mixed-case, EIP-55 checksummed) while keeping a valid signature for that address on each message.
3. Assert that, with a correctly fixed implementation, the (N - burst) requests after the burst are rejected by rate limiting (i.e., no `MethodSecretsSet`/`MethodHeartbeat` handling occurs and no storage writes/heartbeat goroutines are triggered), whereas in the current code all N requests succeed because each casing variant opens a new rate-limiter bucket.

### Citations

**File:** core/services/functions/connector_handler.go (L125-140)
```go
func (h *functionsConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		h.lggr.Errorw("failed to decode request", "id", gatewayID, "err", err)
		return nil
	}
	body := &msg.Body
	fromAddr := ethCommon.HexToAddress(body.Sender)
	if !h.allowlist.Allow(fromAddr) {
		h.lggr.Errorw("allowlist prevented the request from this address", "id", gatewayID, "address", fromAddr)
		return nil
	}
	if !h.rateLimiter.Allow(body.Sender) {
		h.lggr.Errorw("request rate-limited", "id", gatewayID, "address", fromAddr)
		return nil
	}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L175-179)
```go
func (a *onchainAllowlist) Allow(address common.Address) bool {
	allowlist := *a.allowlist.Load()
	_, ok := allowlist[address]
	return ok
}
```
