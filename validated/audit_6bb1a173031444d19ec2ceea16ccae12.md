### Title
Attacker-controlled `MessageId` collision causes cross-request response hijacking in WebAPI capability callbacks - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
The `handler.savedCallbacks` map is keyed solely by the client-supplied `msg.Body.MessageId`, with no per-sender scoping and no duplicate/uniqueness check when a new callback is registered in `HandleLegacyUserMessage`. Any two unprivileged gateway clients who submit requests using the same `MessageId` can cause the gateway to overwrite one client's pending callback with the other's, so a subsequent node response for the first request is delivered to the second client's callback in `handleWebAPITriggerMessage`.

### Finding Description
In `HandleLegacyUserMessage` [1](#0-0) , the handler unconditionally does:
```go
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
```
with no check for an existing entry under the same key. `msg.Body.MessageId` is fully attacker/client-controlled: it is part of the signed `api.Message` body that the requester constructs and signs themselves [2](#0-1) , and `Validate()` only checks length/null-byte constraints on `MessageId`, never uniqueness or binding to `Sender` [3](#0-2) .

When a node later responds, `handleWebAPITriggerMessage` looks the callback up purely by `MessageId` and delivers the response to whatever callback is currently stored there:
```go
savedCb, found := h.savedCallbacks[msg.Body.MessageId]
delete(h.savedCallbacks, msg.Body.MessageId)
...
return savedCb.SendResponse(...)
``` [4](#0-3) 

There is no check that the responding node's message corresponds to the original requester who registered that `MessageId` — only that the node's own signature/address matches `nodeAddr` in `HandleNodeMessage` [5](#0-4) . Because two independent, unprivileged clients (any user of the gateway's WebAPI trigger method) can choose identical `MessageId` values in their own signed requests, the second registration silently overwrites the first client's `savedCallback` entry in the shared `map[string]*savedCallback`. When the node eventually answers the first (victim) request, the gateway delivers that response to the second (attacker) client's callback, and the victim's callback is left dangling until it times out via `pruneCallbacks` [6](#0-5) .

This contrasts with the vault handler, which explicitly rejects colliding request IDs ("request was already authorized previously") as shown in its test suite [7](#0-6) , demonstrating that the capabilities handler lacks an equivalent, already-established safeguard elsewhere in the same codebase.

### Impact Explanation
An unprivileged client of the gateway can hijack another user's callback response by choosing (or racing to submit) a colliding `MessageId`, receiving data/response payloads intended for a different requester (cross-request response confusion / information leak to the wrong party) and denying the victim their legitimate response (their callback silently hangs until pruning/timeout). This is a data-integrity/confidentiality issue between gateway clients that share the same `handler` instance/DON, matching a "misreporting/data tampering" and "unauthorized data disclosure between requesters" class of impact.

### Likelihood Explanation
Exploitation requires no special privilege: any client capable of calling the gateway's WebAPI trigger endpoint can sign and submit a message with an arbitrary `MessageId` of their choosing, and needs only to time their submission so their `MessageId` collides with (or is submitted shortly after) another in-flight request using the same ID, before the node response for the original arrives. Because `MessageId` values are often predictable/short-lived tokens chosen by client code (e.g., trigger event IDs), and no server-side entropy or uniqueness guarantee exists, this is straightforward to reproduce deterministically in a controlled scenario, though real-world success also depends on race timing against the responding node(s).

### Recommendation
Scope `savedCallbacks` keys by `(Sender, MessageId)` (or another value bound to the authenticated signer) instead of `MessageId` alone, and reject/queue registration when a colliding key is already active (as the vault handler already does), rather than silently overwriting the previous callback.

### Proof of Concept
Unit test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Build two valid, differently-signed `api.Message`s (`victimMsg`, `attackerMsg`) both using `MessageId = "collide-1"` via the existing `triggerRequest` helper but with two different node/client keys.
2. Call `handler.HandleLegacyUserMessage(ctx, victimMsg, victimCb)`, then before delivering any node response, call `handler.HandleLegacyUserMessage(ctx, attackerMsg, attackerCb)`.
3. Assert `handler.savedCallbacks["collide-1"]` now references `attackerCb`, not `victimCb`.
4. Simulate the node responding to the *victim's* original request (`nodeResp` built from `victimMsg`) via `handler.HandleNodeMessage(...)`.
5. Assert that `attackerCb.Wait(ctx)` receives the response (proving cross-request hijack) while `victimCb.Wait(ctx)` never resolves (times out/hangs), demonstrating the lack of isolation between the two requesters' callbacks.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-161)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L248-255)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	msg, err := common.ValidatedMessageFromResp(resp)
	if err != nil {
		return err
	}
	if msg.Body.Sender != nodeAddr {
		return errors.New("message sender mismatch when reading from node ")
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-312)
```go
func (h *handler) pruneCallbacks() {
	h.mu.Lock()
	defer h.mu.Unlock()

	// First, remove expired callbacks.
	maxAge := time.Duration(h.config.CallbackMaxAgeSec) * time.Second
	now := time.Now()
	var expired int
	for id, cb := range h.savedCallbacks {
		if now.Sub(cb.createdAt) > maxAge {
			delete(h.savedCallbacks, id)
			expired++
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/api/message.go (L42-52)
```go
type MessageBody struct {
	MessageId string `json:"message_id"`
	Method    string `json:"method"`
	DonId     string `json:"don_id"`
	Receiver  string `json:"receiver"`
	// Service-specific payload, decoded inside the Handler.
	Payload json.RawMessage `json:"payload,omitempty"`

	// Fields only used locally for convenience. Not serialized.
	Sender string `json:"-"`
}
```

**File:** core/services/gateway/api/message.go (L54-88)
```go
func (m *Message) Validate() error {
	if m == nil {
		return errors.New("nil message")
	}
	if len(m.Signature) != MessageSignatureHexEncodedLen {
		return errors.New("invalid hex-encoded signature length")
	}
	if len(m.Body.MessageId) == 0 || len(m.Body.MessageId) > MessageIdMaxLen {
		return errors.New("invalid message ID length")
	}
	if strings.HasSuffix(m.Body.MessageId, NullChar) {
		return errors.New("message ID ending with null bytes")
	}
	if len(m.Body.Method) == 0 || len(m.Body.Method) > MessageMethodMaxLen {
		return errors.New("invalid method name length")
	}
	if strings.HasSuffix(m.Body.Method, NullChar) {
		return errors.New("method name ending with null bytes")
	}
	if len(m.Body.DonId) == 0 || len(m.Body.DonId) > MessageDonIdMaxLen {
		return errors.New("invalid DON ID length")
	}
	if strings.HasSuffix(m.Body.DonId, NullChar) {
		return errors.New("DON ID ending with null bytes")
	}
	if len(m.Body.Receiver) != 0 && len(m.Body.Receiver) != MessageReceiverLen {
		return errors.New("invalid Receiver length")
	}
	signerBytes, err := m.ExtractSigner()
	if err != nil {
		return err
	}
	m.Body.Sender = utils.StringToHex(string(signerBytes))
	return nil
}
```

**File:** core/services/gateway/handlers/vault/handler_test.go (L720-725)
```go
		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.NoError(t, err)

		// send duplicate request
		err = h.HandleJSONRPCUserMessage(t.Context(), validJSONRequest, callback)
		require.ErrorContains(t, err, "request was already authorized previously")
```
