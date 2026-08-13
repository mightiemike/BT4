### Title
Cross-User Request-ID Griefing in Gateway `ConfidentialRelayHandler.HandleJSONRPCUserMessage` - (File: `core/services/gateway/handlers/confidentialrelay/handler.go`)

### Summary
The gateway's `ConfidentialRelayHandler` keys pending requests by the caller-supplied JSON-RPC `req.ID` in a single global map shared across all callers, and rejects any new request whose ID matches one already in flight. Because the ID is fully attacker-controlled and not bound to caller identity, an unprivileged user can pre-empt another user's request by first submitting a request with the same ID, causing the legitimate request to be rejected — the same "ID collision griefing" root cause described in the reported `createUserLoan` front-running finding, adapted from on-chain gas-price racing to off-chain request racing on the gateway.

### Finding Description
`HandleJSONRPCUserMessage` accepts any string `req.ID` (up to 200 chars) directly from the untrusted caller with no requirement that it be unique per-caller or bound to caller identity: [1](#0-0) 

The ID is used as the sole key into a shared, global `activeRequests` map: [2](#0-1) 

If `h.activeRequests[req.ID] != nil`, i.e. the ID is already in-flight (submitted by *any* caller, not necessarily the same one), the handler immediately rejects the new request with `"request ID already exists"` and never forwards it to nodes: [3](#0-2) 

This is directly analogous to the `LoanManager.createUserLoan` root cause: a caller-chosen identifier is used as a global uniqueness key without being derived from (or bound to) the caller's own identity, so any other caller who front-runs the identifier blocks the legitimate owner's operation. Unlike the on-chain report (which relies on gas-price ordering within a single block), here the race is a simple ordering race at the gateway's in-memory dispatch layer — an attacker simply needs to observe or guess a victim's chosen request ID and submit their own request with that same ID microseconds earlier (or repeatedly spam-register likely/observed IDs) to win the map slot.

Confirmed by the existing test that documents this exact behavior: [4](#0-3) 

The vault gateway handler has the structurally identical pattern — `newActiveRequest` in `core/services/gateway/handlers/vault/handler.go` also keys `activeRequests` by the caller-supplied `req.ID` and rejects duplicates the same way: [5](#0-4) 
However, in the vault handler, request authorization/owner binding happens earlier via `h.requestProcessor.ProcessRequest`, and the code elsewhere strips an owner-scoped ID prefix (`vaulttypes.RequestIDSeparator`) before returning the response to the user, which suggests the vault path may already namespace IDs by authenticated owner before they reach `newActiveRequest`. I could not fully confirm from available code whether this owner-prefixing is applied to `req.ID` before the uniqueness check runs, so the vault path's exposure is uncertain and not asserted here. The confidential-relay handler, by contrast, performs **no such authorization or owner-binding step** before calling `newActiveRequest` — `HandleJSONRPCUserMessage` goes straight to `newActiveRequest`/`fanOutToNodes` with only length/emptiness validation on `req.ID`.

### Impact Explanation
An attacker who can send requests to the gateway's `confidential-compute-relay` service (an unprivileged, externally-reachable JSON-RPC endpoint, per `gateway.go`'s `ProcessRequest` routing) can:
- Deny a specific in-flight request from completing by claiming its ID first, causing `HandleJSONRPCUserMessage` to return `"request ID already exists"` to the legitimate caller.
- Or persistently occupy commonly-used/predictable ID patterns to block legitimate `MethodSecretsGet` / `MethodCapabilityExec` relay requests from ever being admitted, since the map entry only clears on completion/timeout (`defaultRequestTimeoutSec`).

This is a griefing/availability impact against the confidential relay capability-exec path, consistent with the "Impacts: Griefing" category cited in the original report, and does not require any privilege beyond being able to reach the gateway's public JSON-RPC endpoint.

### Likelihood Explanation
Likelihood is moderate: exploitation requires the attacker to either predict a victim's request ID ahead of time or continuously flood the map with reserved IDs, and the collision window is bounded by request lifetime (the ID is freed once the request completes or the timeout/cleanup sweep runs, e.g. `defaultCleanUpPeriod` / `defaultRequestTimeoutSec`). If request IDs are generated with sufficient entropy (e.g., UUIDs) by well-behaved callers, blind collision is impractical; but nothing in the handler enforces ID unpredictability or per-caller namespacing, so the mitigation currently relies entirely on caller-side ID generation practices rather than server-side design.

### Recommendation
Namespace `activeRequests` keys by authenticated caller/session identity in addition to (or instead of) the raw caller-supplied `req.ID`, e.g. `key := callerIdentity + ":" + req.ID` (mirroring the owner-prefixing pattern already present in the vault handler's `RequestIDSeparator` usage), so that ID collisions can only occur within a single caller's own request stream, not across independent callers. Alternatively, generate/attach an internal, gateway-assigned dedup key (not directly attacker-supplied) for map storage, and use the external `req.ID` only for response correlation.

### Proof of Concept
Given `TestConfidentialRelayHandler_DuplicateRequestID`, the collision requires no shared caller identity, only knowledge of the target `req.ID`:
```go
req := jsonrpc.Request[json.RawMessage]{ID: "req-dup", Method: MethodCapabilityExec, Params: &params}
err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)   // legitimate/attacker request wins the slot
require.NoError(t, err)

cb2 := common.NewCallback()
err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)   // victim request with the same ID
require.ErrorContains(t, err, "request ID already exists") // victim is denied
``` [4](#0-3) 

In production, the attacker only needs to win the race to submit a request carrying the same `req.ID` before the legitimate caller's request is admitted into `h.activeRequests`, exactly mirroring the mempool front-running dynamic described in the original `createUserLoan` report but applied to gateway request ordering instead of blockchain transaction ordering.

### Citations

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-366)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	l := logger.With(h.lggr, "method", req.Method, "requestID", req.ID)
	l.Debugw("handling confidential relay request")

	ar, err := h.newActiveRequest(req, callback)
	if err != nil {
		return err
	}

	return h.fanOutToNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler_test.go (L767-785)
```go
func TestConfidentialRelayHandler_DuplicateRequestID(t *testing.T) {
	t.Parallel()
	h, cb, don, _ := setupHandler(t, 4)
	don.On("SendToNode", mock.Anything, mock.Anything, mock.Anything).Return(nil)

	params := json.RawMessage(`{"workflow_id":"wf1"}`)
	req := jsonrpc.Request[json.RawMessage]{
		ID:     "req-dup",
		Method: MethodCapabilityExec,
		Params: &params,
	}

	err := h.HandleJSONRPCUserMessage(t.Context(), req, cb)
	require.NoError(t, err)

	cb2 := common.NewCallback()
	err = h.HandleJSONRPCUserMessage(t.Context(), req, cb2)
	require.ErrorContains(t, err, "request ID already exists")
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L466-481)
```go
func (h *handler) newActiveRequest(req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) (*activeRequest, error) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.activeRequests[req.ID] != nil {
		h.lggr.Errorw("request id already exists", "requestID", req.ID)
		return nil, errors.New("request ID already exists: " + req.ID)
	}
	ar := &activeRequest{
		Callback:  callback,
		req:       req,
		createdAt: h.clock.Now(),
		responses: map[string]*jsonrpc.Response[json.RawMessage]{},
	}
	h.activeRequests[req.ID] = ar
	return ar, nil
}
```
