### Title
Integer underflow in stale-message check allows negative `Timestamp` values to bypass freshness/replay validation - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` validates message freshness using `uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp)`, but `payload.Timestamp` is fully attacker-controlled and cast from a signed type to `uint` without validating for negative values. A crafted negative `Timestamp` wraps around to a huge `uint` value, causing the comparison to always evaluate as "not stale" regardless of actual age, bypassing the intended freshness/replay check.

### Finding Description
In `HandleLegacyUserMessage` [1](#0-0) , the handler unmarshals the attacker-supplied `webapicap.TriggerRequestPayload` from `body.Payload` directly from the incoming JSON-RPC message, meaning `payload.Timestamp` is fully attacker-controlled.

The code only explicitly rejects `payload.Timestamp == 0` [2](#0-1) , and then performs the freshness check:

```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
``` [3](#0-2) 

Because `payload.Timestamp` is a signed integer type and the comparison casts it with `uint(...)`, any negative (or otherwise out-of-range) value supplied by the attacker will wrap around to a very large `uint` value (near `math.MaxUint64` on 64-bit platforms, since Go's `uint`/`int` are platform word size). Since the right-hand side of the inequality becomes enormous, the left-hand side (`uint(now) - MaxAllowedMessageAgeSec`, which is on the order of the current Unix timestamp, ~1.7e9–2e9) will never exceed it, so the "stale message" branch is never triggered — the message is always treated as fresh, regardless of its actual (invalid/very old/negative) timestamp.

This is exploitable purely from attacker-controlled JSON input (`payload.Timestamp`), independent of any misconfiguration of `MaxAllowedMessageAgeSec`, satisfying the "attacker fully controls Timestamp" precondition.

After passing this broken freshness check, the request proceeds unchecked to signature/transform validation and is broadcast to all DON member nodes via `don.SendToNode` [4](#0-3) , with no other timestamp-based replay protection present in this handler.

### Impact Explanation
An attacker who can submit a `web_api_trigger` message with a crafted negative `Timestamp` bypasses the intended staleness/replay window entirely. Combined with a captured/replayed valid signature+payload (or if signature validation does not independently bind timestamp freshness), this allows previously-valid trigger requests to be resubmitted and accepted as "fresh" indefinitely, causing repeated/unintended DON job (workflow trigger) execution. This matches a data-integrity / unauthorized-workflow-execution class of impact under the Chainlink bug bounty (replay leading to unintended contract/workflow execution).

### Likelihood Explanation
The precondition is minimal: an attacker who can send an otherwise well-formed `TriggerRequestPayload` (which requires being able to reach `HandleLegacyUserMessage`, e.g., via the gateway's user-message API, potentially requiring existing message authorization but not elevated privileges) merely needs to set `Timestamp` to a negative value like `-1`. This is trivial to craft and passes the `== 0` explicit check, making exploitation straightforward and repeatable.

### Recommendation
Perform the freshness comparison using a signed/64-bit time-delta computation that explicitly rejects negative or out-of-range timestamps before any unsigned cast, e.g.:
```go
now := time.Now().Unix()
if payload.Timestamp <= 0 || payload.Timestamp > now || now-payload.Timestamp > int64(h.config.MaxAllowedMessageAgeSec) {
    // reject as stale/invalid
}
```
This avoids all unsigned wraparound conditions for both `payload.Timestamp` and `MaxAllowedMessageAgeSec`.

### Proof of Concept
Add a fuzz/unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Configure handler with `MaxAllowedMessageAgeSec: 30`.
2. Construct a `webapicap.TriggerRequestPayload` with `Timestamp = -1` (or other negative/near-boundary values).
3. Call `HandleLegacyUserMessage` with this payload.
4. Assert that the callback receives a "stale message" / rejection error response (expected/correct behavior) — the current implementation will instead proceed to call `don.SendToNode` for all DON members, demonstrating the bypass.
5. Extend as a fuzz test over `int64` values of `Timestamp` (including negative, `math.MinInt64`, and values just below/above `now`) plus varying `MaxAllowedMessageAgeSec`, comparing the handler's accept/reject decision against a reference big-integer (non-wrapping) implementation: `is_stale := payload.Timestamp <= 0 || now - payload.Timestamp > int64(MaxAllowedMessageAgeSec)`. Any mismatch confirms the vulnerability.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-357)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L359-370)
```go
	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L372-383)
```go
	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-420)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
