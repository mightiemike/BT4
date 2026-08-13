### Title
Global, unscoped `requestID` map in `httpTriggerHandler` allows any authorized workflow caller to front-run/DoS another workflow's HTTP trigger request - (File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
`httpTriggerHandler.setupCallback` registers every incoming `workflows.execute` trigger request into a single, process-wide map keyed **only** by the user-supplied JSON-RPC `requestID`, with no scoping by workflow ID, workflow owner, or caller identity. Because the ID space is fully attacker-controlled and globally shared, any caller who is authorized to trigger at least one workflow can pre-register a `requestID` that a victim (operating on a completely unrelated workflow) is expected to use, causing the victim's legitimate request to be rejected with a "conflict" error. This mirrors the `registerFunctionSelector` bug class in the referenced report: a short/attacker-controlled key registered into a single shared namespace on a first-come-first-served basis, enabling front-running/DoS/griefing of a legitimate registration.

### Finding Description
`HandleUserTriggerRequest` validates the request, resolves the workflow, authorizes the caller against *that* workflow's key, and then calls `setupCallback`: [1](#0-0) 

The `callbacks` map is declared as `map[string]savedCallback // requestID -> savedCallback` with no workflow/owner component in the key: [2](#0-1) 

`validateRequestID` only rejects empty IDs and IDs containing `/` (reserved for internal node routing); it does not scope or namespace the ID to the workflow or caller: [3](#0-2) 

Authorization (`authorizeRequest`) is performed per-*workflow* (the caller must have a valid key/signature for the specific `workflowID` being triggered), but this only proves the caller can trigger *that* workflow - it says nothing about the `requestID` value they choose, which remains attacker-controlled and checked against the global map: [4](#0-3) 

If the `requestID` is already present in the shared map, the request is rejected with `jsonrpc.ErrConflict` regardless of which workflow it belongs to:
```
h.handleUserError(ctx, requestID, jsonrpc.ErrConflict,
    fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
```
This is confirmed by the existing test that only checks same-workflow duplicate submission, not cross-workflow collision protection: [5](#0-4) 

Because any account holding a signing key for *any* workflow it controls is sufficient to reach `setupCallback` and register an ID in the shared map, an attacker does not need any privilege over the victim's workflow - they only need to be an authorized (but otherwise unprivileged) caller of their own, unrelated workflow.

### Impact Explanation
An attacker who can predict or observe a victim's chosen `requestID` (e.g., sequential IDs, UUIDs the attacker can guess/observe from prior traffic/logs, or IDs following a client-known convention) can pre-register that ID against their own authorized workflow just before the victim's request arrives. The victim's legitimate `workflows.execute` request will then be rejected with `jsonrpc.ErrConflict` ("requestID has already been used"), denying execution of their workflow trigger. This is a Denial-of-Service / griefing vector against the Gateway's HTTP trigger capability that crosses trust boundaries between unrelated, mutually untrusted workflow owners who merely share the same Gateway DON.

### Likelihood Explanation
Likelihood is moderate: the attacker must (1) have valid authorization/signing capability for at least one workflow registered on the Gateway (a normal, unprivileged capability many CRE users have) and (2) know or predict the victim's `requestID` value ahead of time. Request IDs are often deterministic or low-entropy in client SDKs (sequence counters, timestamps, or reused idempotency keys), making prediction plausible in many real deployments, though a fully random/high-entropy client-chosen ID would reduce the practical likelihood, similar to how the front-running attack against `registerFunctionSelector` was ultimately deemed a "punted" griefing vector rather than removed.

### Recommendation
Scope the callback map key by workflow identity (e.g., `workflowID + separator + requestID`, similar to the pattern already used elsewhere in the gateway such as `vaulttypes.RequestIDSeparator` prefixing with `owner`), so that a `requestID` collision can only occur within the same workflow/owner's own request stream, not across unrelated callers. This directly parallels the recommended fix for the analogous MUD issue (changing the collision-prone shared namespace so that different actors cannot collide with each other), while accepting that within a single workflow's own request stream, the caller is responsible for choosing unique IDs.

### Proof of Concept
1. Attacker registers/owns Workflow A (authorized to sign/trigger requests against it).
2. Attacker observes or predicts that Workflow B's legitimate operator will send a `workflows.execute` request with `requestID = "X"` (e.g., a sequential counter, timestamp-based ID, or an ID reused from a public API contract).
3. Attacker sends a valid, authorized `workflows.execute` request for Workflow A with `id = "X"` slightly before the victim's request for Workflow B is processed. This call succeeds and inserts `"X"` into the shared `h.callbacks` map via `setupCallback`.
4. When the victim's legitimate request for Workflow B arrives with the same `requestID = "X"`, `setupCallback` finds `"X"` already present and returns `jsonrpc.ErrConflict`, causing `HandleUserTriggerRequest` to reject the victim's workflow trigger entirely - denying the victim's execution.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L51-64)
```go
type httpTriggerHandler struct {
	services.StateMachine
	config                  ServiceConfig
	don                     handlers.DON
	donConfig               *config.DONConfig
	lggr                    logger.Logger
	callbacksMu             sync.Mutex
	callbacks               map[string]savedCallback // requestID -> savedCallback
	stopCh                  services.StopChan
	workflowMetadataHandler *WorkflowMetadataHandler
	userRateLimiter         limits.RateLimiter
	metrics                 *metrics.Metrics
	wg                      sync.WaitGroup
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L181-193)
```go
func (h *httpTriggerHandler) validateRequestID(ctx context.Context, requestID string, callback handlers.Callback) error {
	if requestID == "" {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "'id' field is required and cannot be empty. Use a new unique request 'id' for each request", callback)
		return errors.New("empty request ID")
	}
	// Request IDs from users must not contain "/", since this character is reserved
	// for internal node-to-node message routing (e.g., "http_action/{workflowID}/{uuid}").
	if strings.Contains(requestID, "/") {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "request ID must not contain '/'", callback)
		return errors.New("request ID must not contain '/'")
	}
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L359-367)
```go
func (h *httpTriggerHandler) authorizeRequest(ctx context.Context, workflowID string, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback) (*gateway_common.AuthorizedKey, error) {
	h.lggr.Debugw("authorizing request", "workflowID", workflowID, "requestID", req.ID)
	key, err := h.workflowMetadataHandler.Authorize(workflowID, req.Auth, req)
	if err != nil {
		h.handleUserError(ctx, req.ID, jsonrpc.ErrInvalidRequest, "Auth failure: "+err.Error(), callback)
		return nil, errors.Join(errors.New("auth failure"), err)
	}
	return key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L397-422)
```go
func (h *httpTriggerHandler) setupCallback(ctx context.Context, requestID string, callback handlers.Callback, requestStartTime time.Time) (<-chan struct{}, error) {
	h.callbacksMu.Lock()
	defer h.callbacksMu.Unlock()

	if _, found := h.callbacks[requestID]; found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrConflict, fmt.Sprintf("requestID: %s has already been used. Ensure the requestID is unique for each request.", requestID), callback)
		return nil, fmt.Errorf("in-flight request ID: %s", requestID)
	}

	// (N+F)//2 + 1 threshold where N = number of nodes, F = number of faulty nodes
	threshold := (len(h.donConfig.Members)+h.donConfig.F)/2 + 1
	agg, err := aggregation.NewIdenticalNodeResponseAggregator(threshold)
	if err != nil {
		return nil, errors.New("failed to create response aggregator: " + err.Error())
	}

	doneCh := make(chan struct{})
	h.callbacks[requestID] = savedCallback{
		Callback:           callback,
		requestStartTime:   requestStartTime,
		createdAt:          time.Now(),
		responseAggregator: agg,
		doneCh:             doneCh,
	}
	return doneCh, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler_test.go (L316-354)
```go
	t.Run("duplicate request ID", func(t *testing.T) {
		handler, mockDon := createTestTriggerHandler(t)
		privateKey := createTestPrivateKey(t)
		registerWorkflow(t, handler, workflowID, privateKey)
		callback1 := hc.NewCallback()
		callback2 := hc.NewCallback()

		triggerReq := gateway_common.HTTPTriggerRequest{
			Workflow: gateway_common.WorkflowSelector{
				WorkflowID: workflowID,
			},
			Input: []byte(`{"key": "value"}`),
		}
		reqBytes, err := json.Marshal(triggerReq)
		require.NoError(t, err)

		rawParams := json.RawMessage(reqBytes)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      requestID,
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &rawParams,
		}
		// First request should succeed
		req.Auth = createTestJWTToken(t, req, privateKey)
		mockDon.EXPECT().SendToNode(mock.Anything, mock.Anything, mock.Anything).Return(nil).Times(3)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback1, time.Now())
		require.NoError(t, err)

		// Second request with same ID should fail
		req.Auth = createTestJWTToken(t, req, privateKey)
		err = handler.HandleUserTriggerRequest(t.Context(), req, callback2, time.Now())
		require.Error(t, err)
		require.Contains(t, err.Error(), "in-flight request")

		r, err := callback2.Wait(t.Context())
		require.NoError(t, err)
		requireUserErrorSent(t, r, jsonrpc.ErrConflict)
	})
```
