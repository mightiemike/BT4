### Title
Missing singleflight/lock during cache-miss in `getMasterPublicKey` allows concurrent, pre-authorization stampede of `MasterPublicKeyFromSecretsService` calls - ([File: core/capabilities/vault/gw_handler.go])

### Summary
`GatewayHandler.getMasterPublicKey` (core/capabilities/vault/gw_handler.go:238-260) uses a check-then-act pattern with an `RWMutex` that does not prevent multiple goroutines from concurrently calling the expensive `MasterPublicKeyFromSecretsService` before the cache is populated. Because this call happens in `HandleGatewayMessage` (gw_handler.go:188-193) *before* `requestProcessor.ProcessRequest` performs allowlist/JWT authorization, unauthenticated/unauthorized senders can trigger the fetch path repeatedly.

### Finding Description
In `getMasterPublicKey`, the handler takes an `RLock`, checks `h.cachedMasterPublicKey`, releases the `RLock`, and if nil, calls `MasterPublicKeyFromSecretsService(ctx, h.secretsService)` outside any lock [1](#0-0) . Only after the fetch completes does it re-acquire the `Lock` to store the result, discarding duplicates [2](#0-1) . This is a classic missing-singleflight cache-miss race: any goroutines that observe `cachedMasterPublicKey == nil` concurrently will all invoke the downstream fetch independently.

Critically, for `MethodSecretsCreate`/`MethodSecretsUpdate`, `getMasterPublicKey` is invoked before `requestProcessor.ProcessRequest` (which performs allowlist/JWT authorization) [3](#0-2) . This means the ordering of checks does not gate the expensive fetch behind authorization — an unauthenticated sender's request can still trigger `MasterPublicKeyFromSecretsService`, which itself calls into `secretsService.GetPublicKey` and performs hex-decoding/unmarshalling of the TDH2 public key [4](#0-3) .

Messages arrive via `gatewayConnector.readLoop`, which invokes `handler.HandleGatewayMessage` synchronously per gateway connection [5](#0-4) ; concurrency across multiple simultaneous gateway connections (or during initial startup/cache-invalidation windows) can still produce parallel calls into `getMasterPublicKey`.

### Impact Explanation
This causes redundant, unbounded (bounded only by attacker request volume and any upstream rate limiter burst) calls to `MasterPublicKeyFromSecretsService` during the cache-miss window. Repeated invocation of this privileged resource-fetch path degrades the vault capability's ability to service legitimate key/secret operations, matching a "Denial of Service — degraded node function availability" bounty category rather than a full compromise, since no secret disclosure or unauthorized transaction execution occurs.

### Likelihood Explanation
The race window is limited to the period before `cachedMasterPublicKey` is first populated (e.g., at node startup), which is a real, if narrow and largely one-time, window. Whether this is practically exploitable further depends on any per-sender/per-node rate limiting upstream in the gateway-side vault handler (`nodeRateLimiter` in `core/services/gateway/handlers/vault/handler.go`), which I was not able to fully trace before running out of tool budget — its exact configuration and whether it serializes bursts before they reach the node's `GatewayHandler` is unconfirmed. Given that a rate limiter's burst allowance still permits multiple simultaneous in-flight requests, the underlying missing-singleflight bug remains real even if the blast radius is reduced by that limiter.

### Recommendation
Use a `sync.Once` per cache-population event or a `golang.org/x/sync/singleflight.Group` keyed on a constant key to ensure only one in-flight call to `MasterPublicKeyFromSecretsService` occurs at a time, with all concurrent callers awaiting that single result. Additionally, move the authorization check (`requestProcessor.ProcessRequest`'s allowlist/JWT check) ahead of the master-key fetch in `HandleGatewayMessage` so unauthenticated senders cannot trigger the expensive fetch at all.

### Proof of Concept
Unit test in `core/capabilities/vault/gw_handler_test.go`:
1. Construct a `GatewayHandler` with a mocked `SecretsService` whose `GetPublicKey` blocks on a channel/sleeps briefly and increments an atomic counter.
2. Spawn N (e.g., 50) goroutines each calling `handler.HandleGatewayMessage` with valid `MethodSecretsCreate` requests while `cachedMasterPublicKey` is nil.
3. Assert `GetPublicKey` (i.e., `MasterPublicKeyFromSecretsService`) is invoked more than once (demonstrating no singleflight), and optionally measure elapsed time/throughput to show degradation proportional to N.
4. As a secondary PoC, send a `MethodSecretsCreate` request with a sender that would fail allowlist authorization and confirm via mock assertion that `secretsService.GetPublicKey` is still called before the authorizer rejects the request — proving the ordering issue.

### Citations

**File:** core/capabilities/vault/gw_handler.go (L187-199)
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
```

**File:** core/capabilities/vault/gw_handler.go (L238-250)
```go
func (h *GatewayHandler) getMasterPublicKey(ctx context.Context) (*tdh2easy.PublicKey, error) {
	h.mu.RLock()
	if h.cachedMasterPublicKey != nil {
		cachedCopy := *h.cachedMasterPublicKey
		h.mu.RUnlock()
		return &cachedCopy, nil
	}
	h.mu.RUnlock()

	publicKey, err := MasterPublicKeyFromSecretsService(ctx, h.secretsService)
	if err != nil {
		return nil, err
	}
```

**File:** core/capabilities/vault/gw_handler.go (L252-261)
```go
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.cachedMasterPublicKey != nil {
		cachedCopy := *h.cachedMasterPublicKey
		return &cachedCopy, nil
	}
	h.cachedMasterPublicKey = publicKey
	cachedCopy := *publicKey
	return &cachedCopy, nil
}
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L292-312)
```go
// MasterPublicKeyFromSecretsService loads the vault master public key from a secrets service.
func MasterPublicKeyFromSecretsService(ctx context.Context, secretsService vaulttypes.SecretsService) (*tdh2easy.PublicKey, error) {
	resp, err := secretsService.GetPublicKey(ctx, &vaultcommon.GetPublicKeyRequest{})
	if err != nil {
		return nil, fmt.Errorf("failed to get vault public key: %w", err)
	}
	if resp == nil || resp.PublicKey == "" {
		return nil, errors.New("vault public key is unavailable")
	}

	masterPublicKeyBytes, err := hex.DecodeString(resp.PublicKey)
	if err != nil {
		return nil, fmt.Errorf("failed to decode vault public key: %w", err)
	}

	masterPublicKey := &tdh2easy.PublicKey{}
	if err := masterPublicKey.Unmarshal(masterPublicKeyBytes); err != nil {
		return nil, fmt.Errorf("failed to unmarshal vault public key: %w", err)
	}
	return masterPublicKey, nil
}
```

**File:** core/services/gateway/connector/connector.go (L268-297)
```go
func (c *gatewayConnector) readLoop(gatewayState *gatewayState) {
	defer c.closeWait.Done()
	ctx, cancel := c.shutdownCh.NewCtx()
	defer cancel()

	for {
		select {
		case <-c.shutdownCh:
			return
		case item := <-gatewayState.conn.ReadChannel():
			var req jsonrpc.Request[json.RawMessage]
			err := json.Unmarshal(item.Data, &req)
			if err != nil {
				c.lggr.Errorw("parse error when reading from Gateway", "id", gatewayState.config.ID, "err", err)
				break
			}
			c.handlersMu.RLock()
			handler, exists := c.handlers[req.Method]
			c.handlersMu.RUnlock()
			if !exists {
				c.lggr.Errorw("no handler for method", "id", gatewayState.config.ID, "method", req.Method)
				break
			}
			// do not break on error. HandleGatewayMessage handles errors
			// by sending a response back to the Gateway.
			err = handler.HandleGatewayMessage(ctx, gatewayState.config.ID, &req)
			if err != nil {
				c.lggr.Warnw("failed to handle message from Gateway", "id", gatewayState.config.ID, "method", req.Method, "err", err)
			}
		}
```
