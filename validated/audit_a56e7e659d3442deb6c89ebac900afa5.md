### Title
Panic via negative-count `strings.Repeat` in `normalizeHex` due to unprefixed max-length hex `workflowID`/`workflowOwner` - ([File: core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go])

### Summary
`validateHexInput` only rejects hex strings whose length exceeds `expectedLength` (which includes the `0x` prefix), so a lowercase hex string with no `0x` prefix but exactly `expectedLength` characters passes validation. `normalizeHex` then computes `expectedHexLength - len(hexStr)` without a prefix stripped (since there was none to strip), producing a negative count passed to `strings.Repeat`, which panics.

### Finding Description
`validateHexInput` (core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go:258-274) checks lowercase, `len(input) > expectedLength`, and hex-decodability after stripping an optional `"0x"` prefix. For `workflowID`, `expectedLength = workflowIDLength = 66` [1](#0-0) , which is meant to represent `0x` + 64 hex chars. If an attacker submits a pure-hex string with **no** `0x` prefix but exactly 66 lowercase hex characters (33 bytes worth of hex, e.g. `"aa...aa"` × 66), the length check `len(input) > 66` is false, and `hex.DecodeString` succeeds because 66 is even, so `validateHexInput` returns nil [2](#0-1) .

This value then flows into `resolveWorkflowID` → `normalizeHex(workflowID, workflowIDLength)` [3](#0-2) . Inside `normalizeHex`:
```go
hexStr := strings.TrimPrefix(input, "0x")       // no "0x" present, hexStr unchanged, len=66
expectedHexLength := length - 2                  // 66-2 = 64
paddedHex := strings.Repeat("0", expectedHexLength-len(hexStr)) + hexStr  // Repeat("0", 64-66) = Repeat("0", -2)
``` [4](#0-3) 
`strings.Repeat` panics when given a negative count. The same defect applies to `workflowOwner` with `expectedLength = workflowOwnerLength = 42`: a 42-char unprefixed pure-hex owner string passes `validateHexInput` and then panics in `normalizeHex` via the `workflowOwner` path at line 345 [5](#0-4) .

This is reachable directly by an unprivileged/unauthenticated user: `HandleUserTriggerRequest` is invoked as the entry point for user-submitted HTTP trigger gateway requests [6](#0-5) , calling `validatedTriggerRequest` (which runs `validateWorkflowID`/`validateWorkflowOwner`) and then `resolveWorkflowID` before any authorization check (`authorizeRequest` happens only afterward) [7](#0-6) . No authentication, signature verification, or authorization gate stands between the raw HTTP body input and the panic-triggering code path. I was unable to fully confirm within available context whether the gateway's HTTP/WS server (`network/httpserver.go`, `network/wsserver.go`) wraps request handling in a panic-recovery middleware; no `recover()` calls were found anywhere under `core/services/gateway/**` except in test files, suggesting the panic is likely unrecovered and would crash the goroutine (and potentially the process if it's on the main goroutine of the request-handling path), but this could not be conclusively verified.

### Impact Explanation
An unauthenticated/unprivileged external actor can send a single crafted JSON-RPC `workflows.execute` request to the gateway's HTTP trigger endpoint with a `workflowID` or `workflowOwner` value that is valid lowercase hex but lacks the `0x` prefix at exactly the maximum length, causing a Go runtime panic in `normalizeHex`. If unrecovered, this results in a crash of the handling goroutine or gateway service, constituting a remotely triggerable Denial of Service against the Chainlink Gateway/DON trigger-handling path — matching a "Node crash / DoS via malformed workflow gateway request" impact class.

### Likelihood Explanation
Trivially reproducible: requires only crafting a plain HTTP JSON-RPC request with a specific-length hex string (no special access, keys, or timing). No rate limiting or authorization occurs before this code path executes (rate limiting and authorization happen after `resolveWorkflowID`). Every request with this shape will panic deterministically.

### Recommendation
In `normalizeHex`, clamp/guard against negative padding lengths, and more robustly, fix `validateHexInput` to require/normalize the `0x` prefix explicitly (e.g., reject inputs without `0x` prefix, or compute expected hex length independent of prefix presence) so `expectedHexLength - len(hexStr)` can never be negative. Add an explicit check: `if padLen := expectedHexLength - len(hexStr); padLen < 0 { return error }` before calling `strings.Repeat`.

### Proof of Concept
Unit test in `http_trigger_handler_test.go`:
```go
func TestNormalizeHex_PanicsOnUnprefixedMaxLengthInput(t *testing.T) {
    input := strings.Repeat("a", 66) // 66 lowercase hex chars, no "0x" prefix
    require.NoError(t, validateHexInput(input, workflowIDLength)) // passes validation
    require.Panics(t, func() {
        normalizeHex(input, workflowIDLength) // strings.Repeat("0", -2) panics
    })
}
```
Integration-level PoC: send a `workflows.execute` JSON-RPC request via `HandleUserTriggerRequest` with `Workflow.WorkflowID` set to a 66-char lowercase hex string without `0x` prefix, and assert the handler panics/the goroutine crashes instead of returning a JSON-RPC error response to the callback.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L36-37)
```go
	workflowIDLength       = 66 // 0x + 64 hex characters = 32 bytes
	workflowOwnerLength    = 42 // 0x + 40 hex characters = 20 bytes
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L86-100)
```go
func (h *httpTriggerHandler) HandleUserTriggerRequest(ctx context.Context, req *jsonrpc.Request[json.RawMessage], callback handlers.Callback, requestStartTime time.Time) error {
	triggerReq, err := h.validatedTriggerRequest(ctx, req, callback)
	if err != nil {
		return err
	}

	workflowID, err := h.resolveWorkflowID(ctx, triggerReq, req.ID, callback)
	if err != nil {
		return err
	}

	key, err := h.authorizeRequest(ctx, workflowID, req, callback)
	if err != nil {
		return err
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L258-274)
```go
func validateHexInput(input string, expectedLength int) error {
	if input != strings.ToLower(input) {
		return errors.New("must be lowercase")
	}

	if len(input) > expectedLength {
		return fmt.Errorf("hex string too long: expected at most %d characters, got %d", expectedLength, len(input))
	}

	hexStr := strings.TrimPrefix(input, "0x")
	_, err := hex.DecodeString(hexStr)
	if err != nil {
		return errors.New("must be a valid hex string")
	}

	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L324-331)
```go
// normalizeHex normalizes a hex string by stripping 0x prefix, padding with leading zeros, and adding 0x prefix back
func normalizeHex(input string, length int) string {
	hexStr := strings.TrimPrefix(input, "0x")
	// length-2 because we'll add "0x" prefix
	expectedHexLength := length - 2
	paddedHex := strings.Repeat("0", expectedHexLength-len(hexStr)) + hexStr
	return "0x" + paddedHex
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L333-337)
```go
func (h *httpTriggerHandler) resolveWorkflowID(ctx context.Context, triggerReq *jsonrpc.Request[gateway_common.HTTPTriggerRequest], requestID string, callback handlers.Callback) (string, error) {
	h.lggr.Debugw("resolving workflow ID", "workflowID", triggerReq.Params.Workflow.WorkflowID, "workflowOwner", triggerReq.Params.Workflow.WorkflowOwner, "workflowName", triggerReq.Params.Workflow.WorkflowName, "workflowTag", triggerReq.Params.Workflow.WorkflowTag, "requestID", requestID)
	workflowID := triggerReq.Params.Workflow.WorkflowID
	if workflowID != "" {
		workflowID = normalizeHex(workflowID, workflowIDLength)
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L345-345)
```go
	workflowOwner := normalizeHex(triggerReq.Params.Workflow.WorkflowOwner, workflowOwnerLength)
```
