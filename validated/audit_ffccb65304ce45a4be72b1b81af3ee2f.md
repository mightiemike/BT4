### Title
TOCTOU race in JWT replay cache allows duplicate-jti requests to bypass replay protection - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks for JWT replay via `jwtCache.isReplay(claims.ID)` and only records the jti via `jwtCache.recordUsage(claims.ID)` after the authorized-signer check, with no atomicity between the two calls. An attacker who submits two concurrent trigger requests carrying the same JWT (same `jti`, different JSON-RPC request IDs) can have both requests pass the replay check before either one records usage, causing the JWT to be accepted twice.

### Finding Description
In `Authorize`, the check-then-act sequence is: [1](#0-0) 

Specifically, `isReplay` takes an `RLock`/`RUnlock` on `jwtCache.mu` and returns immediately, and `recordUsage` is only called at the end of the function after several other checks (workflow lookup, signer authorization) — a separate `Lock`/`Unlock` cycle: [2](#0-1) 

Because `isReplay` and `recordUsage` are two independent lock acquisitions rather than one atomic check-and-set operation, two goroutines calling `Authorize` concurrently with a JWT containing the same `jti` can both observe `isReplay(jti) == false`, both pass the signer-authorization check, and both call `recordUsage(jti)` — meaning both requests are treated as authorized.

This is reachable from an unprivileged caller through `httpTriggerHandler.HandleUserTriggerRequest` → `authorizeRequest` → `WorkflowMetadataHandler.Authorize`, with no external mutex serializing calls per-jti or per-signer: [3](#0-2) 

Note that request-ID uniqueness is separately enforced via `callbacksMu`/`callbacks` map in `setupCallback` (rejecting duplicate `requestID`s), but this check happens **after** `authorizeRequest`, and it guards against duplicate request IDs, not duplicate JWTs/jti values on distinct request IDs. An attacker can reuse the same signed JWT across two distinct JSON-RPC request IDs, so the request-ID dedup does not prevent this race.

This contrasts with the vault package's `RequestReplayGuard`, which implements an atomic `CheckAndRecord` under a single lock specifically to avoid this class of bug: [4](#0-3) 

### Impact Explanation
Successful exploitation allows a single valid JWT (tied to one signer's identity/authorization for a workflow) to authorize more than one workflow-trigger execution, violating the stated JWT replay-protection invariant ("JWT token has already been used..."). Scoped impact is unauthorized duplicate/replayed workflow execution attributed to another signer's identity — i.e., execution amplification bypassing the intended one-time-use guarantee of a signed request. This does not grant privilege escalation or secret disclosure, but does violate an explicit anti-replay security control on the gateway's workflow-trigger authorization path.

### Likelihood Explanation
Exploitability requires only: (1) possession of one validly signed JWT for a workflow trigger request (already assumed to exist for a legitimate single request, not attacker-privileged), and (2) sending two requests with distinct JSON-RPC IDs but that JWT concurrently to the gateway HTTP trigger endpoint. No node-operator privilege or key compromise is needed beyond what's needed to make one legitimate request. The race window exists between `isReplay` and `recordUsage`, and it is a narrow but real window under real network concurrency (e.g., two near-simultaneous HTTP requests), so it is feasible though timing-dependent, and repeatable via automated concurrent test.

### Recommendation
Replace the two-step `isReplay`/`recordUsage` pattern with a single atomic check-and-set operation under one lock (analogous to the vault package's `RequestReplayGuard.CheckAndRecord`), e.g., add a `checkAndRecord(jti) error` method on `jwtReplayCache` that holds `mu.Lock()` for the full duration of the existence-check-and-insert, and call it once in `Authorize` instead of separate `isReplay`/`recordUsage` calls.

### Proof of Concept
Add a concurrency test to `core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go`:
1. Register a workflow and authorized signer as in existing setup.
2. Create one signed JWT (fixed `jti`) bound to a specific request digest, but instantiate N (e.g., 20) `*jsonrpc.Request[json.RawMessage]` values with distinct `ID` fields but identical `Method`/`Params` (so the digest embedded in the JWT still matches each request, since digest validation is on payload content, not request ID — confirm via `utils.VerifyRequestJWT`).
3. Launch N goroutines simultaneously, each calling `handler.Authorize(workflowID, tokenString, req_i)`, collecting results.
4. Assert: exactly one call returns `err == nil` with a non-nil key; all others return `err != nil` with `require.ErrorContains(t, err, "already been used")`.
5. Run with `go test -race -count=100` to reliably trigger the TOCTOU window; on the current implementation this test is expected to flake/fail (more than one success), demonstrating the vulnerability; after applying the atomic `checkAndRecord` fix, exactly one success should be observed consistently.

### Citations

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L75-103)
```go
func (h *WorkflowMetadataHandler) Authorize(workflowID string, token string, req *jsonrpc.Request[json.RawMessage]) (*gateway.AuthorizedKey, error) {
	claims, signer, err := utils.VerifyRequestJWT(token, *req)
	if err != nil {
		h.lggr.Errorw("Failed to verify JWT", "error", err)
		return nil, err
	}

	if h.jwtCache.isReplay(claims.ID) {
		h.lggr.Warnw("JWT token has already been used", "workflowID", workflowID, "signer", signer.Hex(), "jti", claims.ID)
		return nil, errors.New("JWT token has already been used. Please generate a new one with new id (jti)")
	}

	keys, exists := h.authorizedKeys[workflowID]
	if !exists {
		h.lggr.Errorw("Workflow ID not found in authorized keys", "workflowID", workflowID)
		return nil, fmt.Errorf("workflow ID %s not found", workflowID)
	}
	key := gateway.AuthorizedKey{
		KeyType:   gateway.KeyTypeECDSAEVM,
		PublicKey: strings.ToLower(signer.Hex()),
	}
	if _, exists = keys[key]; !exists {
		h.lggr.Errorw("Signer not found in authorized keys", "signer", signer.Hex())
		return nil, fmt.Errorf("signer '%s' is not authorized for workflow '%s'. Ensure that the signer is registered in the workflow definition", signer.Hex(), workflowID)
	}
	h.jwtCache.recordUsage(claims.ID)

	return &key, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L341-354)
```go
func (cache *jwtReplayCache) isReplay(jti string) bool {
	cache.mu.RLock()
	defer cache.mu.RUnlock()

	_, exists := cache.cache[jti]
	return exists
}

func (cache *jwtReplayCache) recordUsage(jti string) {
	cache.mu.Lock()
	defer cache.mu.Unlock()

	cache.cache[jti] = time.Now()
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

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```
