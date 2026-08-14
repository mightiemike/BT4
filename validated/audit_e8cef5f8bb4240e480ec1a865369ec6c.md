Confirmed: the gateway-side `handler.HandleJSONRPCUserMessage` for `MethodPublicKeyGet` forwards the request to nodes via `h.handlePublicKeyGet` → `h.fanOutToVaultNodes` **without ever validating `req.Params`**, since `MethodPublicKeyGet` is handled before the `vaulttypes.IsGatewaySecretsMethod` check and bypasses `h.requestProcessor.ProcessRequest` entirely [1](#0-0) . The request (with a possibly-nil `Params`) is then relayed unchanged to the node-side `GatewayHandler`, whose `handlePublicKeyGet` immediately dereferences it: `json.Unmarshal(*req.Params, r)` [2](#0-1) . If `req.Params` is `nil` (the JSON-RPC `params` field omitted or `null`), this dereference panics with a nil-pointer dereference, matching the reported bug class (dereferencing a pointer field without checking it, instead of guarding/getter-checking first).

### Title
Nil Pointer Dereference in Vault `handlePublicKeyGet` via Unvalidated `req.Params` - (File: core/capabilities/vault/gw_handler.go)

### Summary
The Vault gateway/node handler path for `vaulttypes.MethodPublicKeyGet` never checks that `req.Params` is non-nil before dereferencing it. An external, unauthenticated caller (public key requests explicitly bypass authorization: "Public key requests don't require authorization") can send a JSON-RPC request for this method with `params` omitted or `null`, causing the node to panic.

### Finding Description
- On the gateway side, `handler.HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` and forwards it directly to the DON nodes via `fanOutToVaultNodes`, skipping `h.requestProcessor.ProcessRequest` (the only place that performs nil-`Params` validation for other methods) [3](#0-2) .
- On the node side, `GatewayHandler.HandleGatewayMessage` routes `vaulttypes.MethodPublicKeyGet` to `h.handlePublicKeyGet` directly, again with no `req.Params == nil` guard, unlike `MethodSecretsCreate/Update/Delete/List` which all go through `h.requestProcessor.ProcessRequest` first [4](#0-3) .
- `handlePublicKeyGet` immediately does `json.Unmarshal(*req.Params, r)`, unconditionally dereferencing the `*json.RawMessage` pointer [2](#0-1) .
- This is the same root-cause pattern as the referenced bug: a pointer-typed field originating from an untrusted request is dereferenced directly instead of being nil-checked (or accessed via a safe getter) first.

### Impact Explanation
A crafted request with method `vaulttypes.MethodPublicKeyGet` and `params: null` (or the field omitted) reaching the node's `GatewayHandler.HandleGatewayMessage` will panic on `*req.Params`. Depending on whether the panic is recovered at a higher layer (e.g., in the gateway connector's message dispatch goroutine), this can crash the goroutine handling gateway messages or, if unrecovered, the node process — a Denial of Service against the Vault capability node. Because `MethodPublicKeyGet` is intentionally exempt from authorization ("Public key requests don't require authorization"), no authentication or allowlisted workflow ownership is required to trigger it.

### Likelihood Explanation
High likelihood for any deployment exposing the Vault gateway handler: the method is unauthenticated by design, requires no valid `EncryptedSecret`/owner data, and the nil-`Params` request is trivial to construct (a minimal well-formed JSON-RPC envelope with `method` set and `params` omitted). The only mitigating factor is the gateway-side response cache (`getCachedPublicKey`) — a cache hit short-circuits before reaching the node, but a cache miss (e.g., right after startup, cache expiry, or on nodes that haven't cached a response yet) reaches the vulnerable code path on every DON member node.

### Recommendation
Add an explicit nil-check for `req.Params` before dereferencing, for all handlers in both `core/services/gateway/handlers/vault/handler.go` and `core/capabilities/vault/gw_handler.go` (`handlePublicKeyGet`, and defensively `handleSecretsCreate/Update/Delete/List` too), returning a `UserMessageParseError`/`InvalidParamsError` instead of panicking, e.g.:
```go
if req.Params == nil {
    return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, errors.New("missing params"))
}
```
mirroring the pattern already used in `handleSecretsGet` in `core/capabilities/confidentialrelay/handler.go` [5](#0-4) .

### Proof of Concept
1. As an unauthenticated user, send a JSON-RPC request to the Vault gateway HTTP endpoint with `method: "vault.publicKey.get"` (or whatever `vaulttypes.MethodPublicKeyGet` resolves to) and no `params` field (or `"params": null`).
2. Ensure the targeted gateway/node's `cachedPublicKeyGetResponse` is currently empty (e.g., immediately after node/gateway restart, before the periodic 1-minute refresh populates it — see `fetchVaultPublicKey`) so the request is not served from cache.
3. The gateway handler forwards the raw request unchanged to each DON member node via `fanOutToVaultNodes` / `SendToNode`.
4. Each receiving node's `GatewayHandler.HandleGatewayMessage` dispatches to `handlePublicKeyGet`, which executes `json.Unmarshal(*req.Params, r)`; since `req.Params` is `nil`, this dereference panics.

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L403-429)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
	}

	h.lggr.Debugw("handling vault request", "method", req.Method, "requestID", req.ID, "request", req)
	if req.Method == vaulttypes.MethodPublicKeyGet {
		// Public key requests don't require authorization,
		// Let's process this request right away.
		// Note we cache this value quite aggressively so don't need to worry about DoS.
		publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
		if cachedPublicKey == nil {
			// Not found in cache. Fetch from nodes.
			ar, err := h.newActiveRequest(req, callback)
			if err != nil {
				h.lggr.Errorw("failed to create new activeRequest", "error", err)
				return err
			}
			return h.handlePublicKeyGet(ctx, ar)
		}
		h.lggr.Debugw("returning cached public key response")
		return h.handlePublicKeyGetSynchronously(ctx, req, publicKeyResponseBytes, callback)
	}
```

**File:** core/capabilities/vault/gw_handler.go (L187-211)
```go
	switch req.Method {
	case vaulttypes.MethodSecretsCreate, vaulttypes.MethodSecretsUpdate:
		publicKey, pkErr := h.getMasterPublicKey(ctx)
		if pkErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pkErr)
			break
		}
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, publicKey)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}
```

**File:** core/capabilities/vault/gw_handler.go (L364-368)
```go
func (h *GatewayHandler) handlePublicKeyGet(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) *jsonrpc.Response[json.RawMessage] {
	r := &vaultcommon.GetPublicKeyRequest{}
	if err := json.Unmarshal(*req.Params, r); err != nil {
		return h.errorResponse(ctx, gatewayID, req, api.UserMessageParseError, err)
	}
```

**File:** core/capabilities/confidentialrelay/handler.go (L279-281)
```go
	if req.Params == nil {
		return h.errorResponse(ctx, gatewayID, req, jsonrpc.ErrInvalidParams, errors.New("missing params"))
	}
```
