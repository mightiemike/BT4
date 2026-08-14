### Title
Missing upper-bound (future-timestamp) validation in legacy message freshness check allows indefinite replay - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` only rejects messages that are too *old*; it never rejects messages whose `payload.Timestamp` is in the *future*. [1](#0-0)  Because the caller of this legacy endpoint is an unprivileged external user who self-signs their own request (no allowlist/authz is enforced before this check — see the explicit `TODO: apply allowlist and rate-limiting here`), an attacker can set `Timestamp` arbitrarily far in the future so the "stale message" comparison never trips, letting the same request be forwarded to the DON indefinitely. [2](#0-1) 

### Finding Description
The relevant path is: `json.Unmarshal(body.Payload, &payload)` → `payload.Timestamp == 0` check → the freshness comparison → `ValidatedRequestFromMessage` → `don.SendToNode` for every DON member. [3](#0-2) 

The freshness check is:
```go
if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
    // reject as stale
}
```
This inequality only fires when `payload.Timestamp` is *smaller* than `now - MaxAllowedMessageAgeSec`, i.e. it only detects messages that are too old. There is no corresponding check that `payload.Timestamp` is not greater than `now` (or some allowed clock-skew bound). If an attacker sets `payload.Timestamp = time.Now().Unix() + 1e9` (or any sufficiently large future value), the right-hand side (`uint(payload.Timestamp)`) will exceed the left-hand side for a practically unbounded amount of real time, so the "stale message" branch never executes and the request is treated as fresh no matter how long the attacker waits before (re)submitting it.

The message is signed by the caller themselves (this is the "legacy user message" path, not a node-to-node message), and nothing upstream of this function performs an allowlist check — the code explicitly flags this gap with `// TODO: apply allowlist and rate-limiting here`. [2](#0-1)  There is also no nonce/dedup store that would independently prevent reprocessing of the same `MessageId`; `savedCallbacks` is only used to route the eventual DON response back to the caller and is deleted once consumed, not used to reject re-submission. [4](#0-3) 

Separately, the underflow scenario described in the question (`MaxAllowedMessageAgeSec > time.Now().Unix()`) would actually cause the left side to wrap to a huge `uint`, making the comparison *always true*, i.e., it would reject every message as stale (a denial-of-service/self-lockout condition), not a bypass. That specific underflow path does not help an attacker bypass freshness — the real, exploitable defect is the missing future-timestamp upper bound, independent of any overflow.

### Impact Explanation
An attacker who is an ordinary (unauthenticated/unprivileged from the DON's perspective) caller of this legacy gateway endpoint can craft one self-signed message with an inflated future `Timestamp` and replay that exact message to the gateway repeatedly, indefinitely, since the staleness check will never reject it. Each replay causes `don.SendToNode` to dispatch the (identical) trigger request to every member of the DON. This enables duplicate/unauthorized repeated trigger execution across the DON and can be used for resource exhaustion or duplicate workflow/trigger invocation, matching the "unauthorized workflow execution" / duplicate-request-replay impact category.

### Likelihood Explanation
Feasibility is high and requires no privileged access: the caller signs their own payload for this legacy user-message path, so they fully control the `Timestamp` field before signing (no need to forge someone else's signature or capture and mutate a third-party message). Combined with the acknowledged absence of allowlisting/rate-limiting at this stage (per the code's own `TODO`), the only obstacle to abuse is the freshness check itself, which this flaw defeats. Repeatability is unlimited — the same crafted message can be resent at any time in the future.

### Recommendation
Add an explicit upper bound (future-timestamp) check in addition to the staleness check, e.g. reject when `payload.Timestamp > uint(time.Now().Unix()) + allowedClockSkewSec`, and consider tracking recently-seen `MessageId`s (or a timestamp+sender+hash tuple) in a bounded replay-protection cache so an otherwise "fresh" message cannot be resent more than once within its validity window. Also prioritize implementing the allowlist/rate-limiting noted in the `TODO`.

### Proof of Concept
Unit test in `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `webapicap.TriggerRequestPayload` with `Timestamp = time.Now().Unix() + 1_000_000_000` (far future) and marshal it as `msg.Body.Payload`.
2. Sign/build the `api.Message` as a normal legacy user message (self-signed by test key) and call `handler.HandleLegacyUserMessage`.
3. Assert that the handler does **not** return the "stale message" error and instead proceeds to call `don.SendToNode` (verify via a mock `handlers.DON`).
4. Repeat the exact same call after advancing a fake clock by e.g. `h.config.MaxAllowedMessageAgeSec * 100` and assert the message is still accepted (never becomes stale), demonstrating indefinite replay validity.
5. Additionally test `MaxAllowedMessageAgeSec` values close to `uint(time.Now().Unix())` to document the separate wrap-around DoS behavior (all messages rejected), confirming it is a distinct, non-bypass issue.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-419)
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
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
```
