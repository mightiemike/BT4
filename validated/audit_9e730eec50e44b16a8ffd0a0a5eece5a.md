### Title
JWT replay guard in `WorkflowMetadataHandler.Authorize` has a non-atomic check-then-record race allowing single-use JWT semantics to be bypassed under concurrent replay - ([File: core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go])

### Summary
`WorkflowMetadataHandler.Authorize` checks JWT replay status and records JWT usage as two separate, non-atomic operations (`isReplay` then `recordUsage`), each independently locking the cache's `sync.RWMutex`. An attacker who captures a single valid signed `req.Auth` JWT can send it concurrently (parallel requests) to the gateway's public trigger endpoint; both requests can pass the `isReplay` check before either calls `recordUsage`, causing the workflow to be triggered more than once from a single signed token. This contrasts with the Vault package's `RequestReplayGuard.CheckAndRecord`, which correctly performs the check-and-record atomically under one lock.

### Finding Description
The call path is `HandleUserTriggerRequest` -> `authorizeRequest` -> `WorkflowMetadataHandler.Authorize`: [1](#0-0) 

`Authorize` performs the replay check and the replay record as two separate calls into `jwtReplayCache`, each independently acquiring/releasing the mutex: [2](#0-1) 

`isReplay` takes a read lock and releases it; `recordUsage` takes a write lock separately later: [3](#0-2) 

Because the "seen?" check and the "mark seen" write are not combined into one atomic critical section, two goroutines processing the same JWT concurrently (e.g., two parallel HTTP POSTs of the identical signed `req.Auth` to the gateway) can both observe `isReplay == false` before either calls `recordUsage`. Both then pass the authorized-signer check and both get forwarded to `sendWithRetries`, resulting in the workflow being triggered twice from one signed, single-use JWT — a bypass of the documented invariant "JWT token has already been used... generate a new one with new id (jti)."

This differs from the equivalent flow in the Vault package, where `RequestReplayGuard.CheckAndRecord` performs the seen-check and the record-write under a single lock acquisition, making it atomic and race-free: [4](#0-3) 

The existing unit test `TestWorkflowMetadataHandler_Authorize`/"JWT replay protection" only exercises sequential (non-concurrent) reuse and therefore does not catch this race: [5](#0-4) 

### Impact Explanation
A captured single-use JWT can, under concurrent replay, trigger the same workflow execution more than once, bypassing the intended single-use JWT/replay-cache invariant. This maps to unauthorized/duplicate workflow execution and potential resource exhaustion or duplicate side effects (e.g., duplicate on-chain writes or external actions triggered by the workflow), consistent with the "unauthorized repeated workflow execution" bounty impact category. The number of duplicate executions achievable is bounded by the race window (limited to a small number of concurrent in-flight requests, not unlimited/arbitrary repetition across the full 24h `JWTReplayPeriodMs` window), which reduces severity relative to a full replay-cache bypass.

### Likelihood Explanation
Exploitability requires only an unprivileged attacker who has observed one legitimate signed request (matching the stated precondition) and the ability to fire multiple concurrent requests with the identical `req.Auth`/params to the public gateway endpoint — no private key or elevated privilege needed. The race window is narrow (the gap between the `isReplay` RLock release and the `recordUsage` Lock acquire, which includes an authorized-keys map lookup in between), so likelihood of hitting the race depends on request timing/concurrency and gateway load, making it feasible but not guaranteed on every attempt.

### Recommendation
Make the replay check-and-record atomic, following the pattern used in `core/capabilities/vault/request_replay_guard.go`: combine `isReplay` and `recordUsage` into a single method (e.g., `CheckAndRecord(jti, expiry)`) that holds the cache's write lock for the entire check-then-write operation, returning an "already used" error if the jti is already present, otherwise recording it before returning success. Update `Authorize` in `workflow_metadata_handler.go` to call this combined atomic method instead of separate `isReplay`/`recordUsage` calls.

### Proof of Concept
Integration/unit test plan (Go, `workflow_metadata_handler_test.go`):
1. Construct one signed JWT/`req.Auth` for a valid `workflowID`/authorized signer as in `TestWorkflowMetadataHandler_Authorize`.
2. Launch two (or more) goroutines that call `handler.Authorize(workflowID, tokenString, req)` concurrently with the exact same token and request.
3. Use a synchronization barrier (e.g., a channel released simultaneously to both goroutines, or artificially delay the interval between `isReplay` and `recordUsage` via a test hook) so both goroutines pass the `isReplay` check before either calls `recordUsage`.
4. Assert that instead of exactly one goroutine succeeding and one failing with "JWT token has already been used", both succeed (demonstrating the race), which violates the single-use invariant. A correct atomic implementation should guarantee exactly one success and one `errors.New("JWT token has already been used...")` regardless of timing.

### Citations

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

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler_test.go (L1093-1117)
```go
	t.Run("JWT replay protection", func(t *testing.T) {
		params := json.RawMessage(`{"test": "data"}`)
		req := &jsonrpc.Request[json.RawMessage]{
			Version: "2.0",
			ID:      "test-request-id-replay",
			Method:  gateway_common.MethodWorkflowExecute,
			Params:  &params,
		}

		token, err := utils.CreateRequestJWT(*req)
		require.NoError(t, err)

		tokenString, err := token.SignedString(privateKey)
		require.NoError(t, err)

		key, err := handler.Authorize(workflowID, tokenString, req)
		require.NoError(t, err)
		require.NotNil(t, key)

		// Second authorization with same JWT should fail (replay attack)
		key, err = handler.Authorize(workflowID, tokenString, req)
		require.Error(t, err)
		require.Contains(t, err.Error(), "JWT token has already been used. Please generate a new one with new id (jti)")
		require.Nil(t, key)
	})
```
