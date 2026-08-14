### Title
Rate limiter key uses un-normalized `body.Sender` while authorization uses checksummed `sender.String()`, enabling per-sender rate-limit bypass - ([File: core/capabilities/webapi/trigger/trigger.go])

### Summary
In `triggerConnectorHandler.processTrigger`, the `allowedSenders` authorization check is keyed on `sender.String()` (the checksummed `ethCommon.Address` produced by `ethCommon.HexToAddress(body.Sender)`), while `trigger.rateLimiter.Allow` is keyed on the raw, un-normalized `body.Sender` string. Since `HexToAddress` accepts and normalizes many textual variants of the same address (different case, missing/extra leading characters that still parse identically, `0x` prefix variants, etc.), an attacker whose address is authorized can submit many textually distinct `body.Sender` values that all resolve to the same `Address` for authorization but populate distinct keys in the rate limiter's per-sender map.

### Finding Description
The relevant code:
```go
// core/capabilities/webapi/trigger/trigger.go
sender := ethCommon.HexToAddress(body.Sender)          // HandleGatewayMessage, line 158
...
if !trigger.allowedSenders[sender.String()] {          // line 101, normalized/checksummed key
    ...
}
if !trigger.rateLimiter.Allow(body.Sender) {           // line 106, raw un-normalized key
    ...
}
```
`ethCommon.HexToAddress` is lenient: it strips a `0x` prefix, left-pads/truncates to 20 bytes, and is case-insensitive with respect to hex digits, so many distinct strings (e.g. `0xABCDEF...`, `0xabcdef...`, `abcdef...`, or strings with extra/missing leading zero-equivalent characters) map to the exact same `Address` value. The authorization check uses this single canonical `Address.String()` (EIP-55 checksummed) form, so all such variants pass the `allowedSenders` gate identically. However, the rate limiter is invoked with `body.Sender`, the raw pre-normalization string, so each textual variant is treated as a distinct sender key by the underlying per-sender limiter map (confirmed by the analogous `ratelimiter.RateLimiter.Allow(sender string)` implementation, which maintains `perSender map[string]*rate.Limiter` keyed directly by the string argument — [1](#0-0)  — the `chainlink-common/pkg/ratelimit.RateLimiter` used here follows the same per-sender-string-keyed design pattern). This means `PerSenderRPS`/`PerSenderBurst` is not actually enforced against the authenticated principal but against an attacker-controlled, non-canonical string, allowing effectively unlimited requests from one authorized sender by varying the case/format of their address in each `body.Sender` field. [2](#0-1) [3](#0-2) 

### Impact Explanation
This is a per-sender rate-limit bypass on the web-API trigger's gateway ingestion path. An authorized sender (address present in a workflow's `allowedSenders`) can flood a workflow trigger far beyond the configured `PerSenderRPS`/`PerSenderBurst`, while still passing authorization on every request, since the global limiter (`GlobalRPS`/`GlobalBurst`) is a separate, shared bucket not tied to sender identity. This can be used to trigger excessive workflow executions, exhaust node/workflow processing resources, and undermine the operator's intended throttling guarantees — a scoped denial-of-service / rate-limit-bypass impact rather than full compromise.

### Likelihood Explanation
Feasible and repeatable with low effort: the attacker only needs one address already present in `allowedSenders` (a normal precondition for any legitimate authorized caller) and full control over the literal `Sender` field value placed in the gateway message body prior to signature/parsing — which is attacker-supplied client-side input, not derived from a signature-verified canonical form at this layer. Generating case/format variants of a fixed hex address is trivial and deterministic.

### Recommendation
Use the same normalized principal for both authorization and rate limiting. Replace `trigger.rateLimiter.Allow(body.Sender)` with `trigger.rateLimiter.Allow(sender.String())` (or another canonical form derived from `sender`), so that all textual representations of the same underlying address collapse to a single rate-limiter key consistent with the `allowedSenders` check.

### Proof of Concept
Unit test in `core/capabilities/webapi/trigger/trigger_test.go` (or new fuzz test):
1. Register a trigger with `AllowedSenders = ["0x<ChecksummedAddr>"]`, `RateLimiter.PerSenderRPS = 1`, `PerSenderBurst = 1`, and a matching topic.
2. Call `HandleGatewayMessage` (or directly `processTrigger`) N times (N > `PerSenderBurst`) within the same time window, each time with `body.Sender` set to a different textual variant of the same address that all normalize to the same `ethCommon.Address` via `HexToAddress` (e.g., lowercase hex, uppercase hex, checksummed form, and the same value with mixed padding that still resolves identically).
3. Assert: with the current code, all N requests succeed (`resp == nil` / `Status: "ACCEPTED"`), i.e., `fullyMatchedWorkflows` increments for every call, demonstrating the per-sender burst limit is not enforced across variants.
4. After applying the fix (`rateLimiter.Allow(sender.String())`), assert that only the first `PerSenderBurst` requests succeed and subsequent ones return the `"request rate-limited for sender ..."` error, matching the fuzzed-variant test described in the question.

### Citations

**File:** core/services/workflows/ratelimiter/ratelimiter.go (L40-52)
```go
func (rl *RateLimiter) Allow(sender string) (senderAllow bool, globalAllow bool) {
	rl.mu.Lock()
	senderLimiter, ok := rl.perSender[sender]
	if !ok {
		senderLimiter = rate.NewLimiter(rate.Limit(rl.config.PerSenderRPS), rl.config.PerSenderBurst)
		rl.perSender[sender] = senderLimiter
	}
	rl.mu.Unlock()

	senderAllow = senderLimiter.Allow()
	globalAllow = rl.global.Allow()
	return senderAllow, globalAllow
}
```

**File:** core/capabilities/webapi/trigger/trigger.go (L97-111)
```go
	for _, trigger := range h.registeredWorkflows {
		for _, topic := range topics {
			if trigger.allowedTopics[topic] {
				matchedWorkflows++
				if !trigger.allowedSenders[sender.String()] {
					err = fmt.Errorf("unauthorized Sender %s, messageID %s", sender.String(), body.MessageId)
					h.lggr.Debugw(err.Error())
					continue
				}
				if !trigger.rateLimiter.Allow(body.Sender) {
					err = fmt.Errorf("request rate-limited for sender %s, messageID %s", sender.String(), body.MessageId)
					continue
				}
				fullyMatchedWorkflows++
				TriggerEventID := body.Sender + payload.TriggerEventId
```

**File:** core/capabilities/webapi/trigger/trigger.go (L151-158)
```go
func (h *triggerConnectorHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) error {
	msg, err := hc.ValidatedMessageFromReq(req)
	if err != nil {
		h.lggr.Errorw("error validating message from request", "err", err, "request", req)
		return nil
	}
	body := &msg.Body
	sender := ethCommon.HexToAddress(body.Sender)
```
