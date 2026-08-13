### Title
Unauthenticated `vault.publicKey.get` cache-miss fast-path allows unbounded DON fan-out and shared rate-limiter exhaustion, degrading vault availability - ([File: core/services/gateway/handlers/vault/handler.go])

### Summary
`HandleJSONRPCUserMessage` special-cases `vaulttypes.MethodPublicKeyGet` and, on a cache miss, calls `h.newActiveRequest` and `h.handlePublicKeyGet` without any authentication, authorization, or per-sender rate limiting, unlike every other vault method which goes through `GatewayVaultRequestProcessor.ProcessRequest`. During any window where `h.cachedPublicKeyGetResponse` is nil (service startup before the first `tickerVaultPublicKeyRefresh` tick, or after repeated `fetchVaultPublicKey` failures), an unauthenticated attacker can flood the endpoint with distinct `req.ID` values to grow `h.activeRequests` unboundedly and force a full DON fan-out per request, exhausting the handler's single shared `nodeRateLimiter` used for all vault node responses. The req.ID content/length itself cannot poison the cached public key, since the cache is populated only from the DON node's `Result` bytes in `tryCachePublicKeyResponse`, independent of request ID.

### Finding Description
In `handler.HandleJSONRPCUserMessage` (core/services/gateway/handlers/vault/handler.go:413-429), the `MethodPublicKeyGet` branch is processed before any call to `h.requestProcessor.ProcessRequest`, which is the only place authorization/validation happens for this handler (line 436). On cache miss, `h.newActiveRequest(req, callback)` (lines 466-481) inserts an entry keyed by `req.ID` into `h.activeRequests` with no cap and no per-sender throttling, and `handlePublicKeyGet` (lines 682-698) then calls `h.fanOutToVaultNodes`, which sends the request to every DON member via `h.don.SendToNode` (lines 726-742).

There is no user/IP rate limiter gating this path — the only rate limiter present, `h.nodeRateLimiter`, is applied in `HandleNodeMessage` (line 493) and is keyed only by `nodeAddr`, shared across *all* vault methods handled by this DON's handler instance (secrets create/update/delete/list and publicKey.get). Each fanned-out `publicKey.get` request produces up to `len(donConfig.Members)` node responses that consume tokens from this same per-node bucket. A flood of unique-`req.ID` requests during the cache-cold window can therefore starve the shared `nodeRateLimiter`, causing legitimate authorized nodes' responses for concurrent secrets operations to be dropped (`l.Debugw("node is rate limited", ...)` at line 494, returning `nil` and silently discarding the response). Entries in `h.activeRequests` persist until `removeExpiredRequests` (5s cleanup ticker, `h.requestTimeout` default 30s) evicts them, so sustained flooding also grows this map unboundedly for the duration of the attack window.

Separately, the concern about cache poisoning via `req.ID` is not substantiated: `tryCachePublicKeyResponse` (lines 539-573) only reads `resp.Result` from the DON's response to populate `h.cachedPublicKeyGetResponse`/`h.cachedPublicKeyObject`; `req.ID` is never used as cache content, only echoed back in the JSON-RPC response envelope. This part of the hypothesis is invalid.

### Impact Explanation
Impact is scoped to availability degradation of the vault gateway: during the cache-cold window (startup, up to the first 1-minute `tickerVaultPublicKeyRefresh` tick, or any period where `fetchVaultPublicKey` fails), an unauthenticated attacker can flood distinct-ID `vault.publicKey.get` requests to (a) grow `h.activeRequests` unboundedly for up to `requestTimeout` seconds per wave, and (b) exhaust the shared per-node `nodeRateLimiter`, causing dropped node responses for legitimate authorized `secrets.create/update/delete/list` operations on the same DON. This matches a DoS impact against the vault gateway's availability for all tenants sharing that DON, but the exposure window is bounded (cold-cache period only, not indefinite), and no secret disclosure or cache poisoning of the returned public key occurs.

### Likelihood Explanation
No node-operator/admin privilege or key leakage is required — only network access to the gateway's public JSON-RPC endpoint, satisfying the unprivileged-attacker precondition. The attack is trivially repeatable (any number of unique `req.ID` values, each ≤200 chars per the length check at line 407) and requires no valid auth token since this branch bypasses `ProcessRequest` entirely. Likelihood is moderated by the fact that the vulnerable window only exists while the public key cache is cold, which is normally brief (first minute at startup, refreshed every minute thereafter) unless `fetchVaultPublicKey` keeps failing.

### Recommendation
Apply a per-sender/global rate limiter (or a dedicated `limits.GateLimiter`) to the `MethodPublicKeyGet` cache-miss branch before calling `h.newActiveRequest`/`h.fanOutToVaultNodes`, independent of the write-methods `nodeRateLimiter`. Additionally, deduplicate concurrent in-flight cache-miss lookups (single-flight) so that many distinct `req.ID`s during a cold cache trigger at most one DON round-trip, and consider using a separate rate limiter (or per-method token buckets) for node responses instead of one limiter shared across all vault methods, so that `publicKey.get` traffic cannot starve `secrets.*` node-response processing.

### Proof of Concept
Integration test plan for `core/services/gateway/handlers/vault/handler_test.go`:
1. Construct a `handler` via `newHandlerWithAuthorizer` with a mock `don` (via `handlermocks.NewDON`) and multiple DON members, and ensure `h.cachedPublicKeyGetResponse` is nil (simulate cold-cache/startup state).
2. Concurrently invoke `h.HandleJSONRPCUserMessage` with N (e.g., 5000) `jsonrpc.Request` objects, each with `Method: vaulttypes.MethodPublicKeyGet` and a unique `ID`, no `Auth` set.
3. Assert: (a) `mockDon.SendToNode` is called `N * len(donConfig.Members)` times (no dedup/rate limiting), (b) `len(h.activeRequests)` grows to N before cleanup runs, confirming unbounded memory growth, and (c) simulate a concurrent legitimate `secrets.list` request and node responses hitting the same `h.nodeRateLimiter`; assert some node responses are dropped (`HandleNodeMessage` returns `nil` due to `!h.nodeRateLimiter.Allow(nodeAddr)`) because the token bucket was exhausted by the flood of `publicKey.get` responses. Also add a unit test asserting `tryCachePublicKeyResponse` output is independent of `req.ID` (varying ID with identical `resp.Result` always yields the same cached bytes) to confirm the cache-poisoning hypothesis is unfounded. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/services/gateway/handlers/vault/handler.go (L370-393)
```go
func (h *handler) removeExpiredRequests(ctx context.Context) {
	h.mu.RLock()
	var expiredRequests []*activeRequest
	now := h.clock.Now()
	for _, userRequest := range h.activeRequests {
		if now.Sub(userRequest.createdAt) > h.requestTimeout {
			expiredRequests = append(expiredRequests, userRequest)
		}
	}
	h.mu.RUnlock()

	for _, er := range expiredRequests {
		responses := er.copiedResponses()
		var nodeResponses strings.Builder
		for nodeKey, nodeResponse := range responses {
			_, _ = fmt.Fprintf(&nodeResponses, "%s ---::: %v               ", nodeKey, nodeResponse)
		}
		nodeResponsesStr := nodeResponses.String()
		err := h.sendResponse(ctx, er, h.errorResponse(er.req, api.RequestTimeoutError, errors.New("request expired without getting quorum of responses from nodes. Available responses: "+nodeResponsesStr), []byte(nodeResponsesStr)))
		if err != nil {
			h.lggr.Errorw("error sending response to user", "requestID", er.req.ID, "error", err)
		}
	}
}
```

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

**File:** core/services/gateway/handlers/vault/handler.go (L489-510)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	if !h.nodeRateLimiter.Allow(nodeAddr) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}

	ar := h.getActiveRequest(resp.ID)
	if ar == nil {
		// Request is not found, so we don't need to send a response to the user
		// This can happen if a slow node responds after the request has already been completed
		l.Debugw("no pending request found for ID")
		return nil
	}

	ok := ar.addResponseForNode(nodeAddr, resp)
	if !ok {
		l.Errorw("duplicate response from node, ignoring", "nodeAddr", nodeAddr)
		return nil
	}
```

**File:** core/services/gateway/handlers/vault/handler.go (L539-573)
```go
func (h *handler) tryCachePublicKeyResponse(resp *jsonrpc.Response[json.RawMessage], l logger.Logger) {
	if resp.Result == nil {
		l.Debugw("no result in public key response, not caching")
		return
	}

	r := &vaultcommon.GetPublicKeyResponse{}
	err := h.unmarshal(bytes.NewReader(*resp.Result), r)
	if err != nil {
		l.Debugw("failed to unmarshal public key response, not caching", "error", err)
		return
	}

	if r.PublicKey == "" {
		l.Debugw("no public key in unmarshaled response, not caching", "response", resp, "result", r)
		return
	}
	masterPublicKey := tdh2easy.PublicKey{}
	masterPublicKeyBytes, err := hex.DecodeString(r.PublicKey)
	if err != nil {
		l.Debugw("failed to decode master public key string", "error", err)
		return
	}
	err = masterPublicKey.Unmarshal(masterPublicKeyBytes)
	if err != nil {
		l.Debugw("failed to unmarshal master public key", "error", err)
		return
	}

	h.mu.Lock()
	h.cachedPublicKeyGetResponse = *resp.Result
	h.cachedPublicKeyObject = &masterPublicKey
	h.mu.Unlock()
	l.Debugw("successfully cached public key response")
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L682-698)
```go
func (h *handler) handlePublicKeyGet(ctx context.Context, ar *activeRequest) error {
	l := logger.With(h.lggr, "method", ar.req.Method, "requestID", ar.req.ID)

	publicKeyResponseBytes, cachedPublicKey := h.getCachedPublicKey()
	if cachedPublicKey != nil {
		l.Debugw("returning cached public key response")
		return h.sendSuccessResponse(ctx, l, ar, &jsonrpc.Response[json.RawMessage]{
			Version: jsonrpc.JsonRpcVersion,
			ID:      ar.req.ID,
			Method:  ar.req.Method,
			Result:  (*json.RawMessage)(&publicKeyResponseBytes),
		})
	}

	l.Debugw("cache stale: forwarding request to nodes", "now", h.clock.Now())
	return h.fanOutToVaultNodes(ctx, l, ar)
}
```

**File:** core/services/gateway/handlers/vault/handler.go (L726-742)
```go
func (h *handler) fanOutToVaultNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var nodeErrors []error
	for _, node := range h.donConfig.Members {
		err := h.don.SendToNode(ctx, node.Address, &ar.req)
		if err != nil {
			nodeErrors = append(nodeErrors, err)
			l.Errorw("error sending request to node", "node", node.Address, "error", err)
		}
	}

	if len(nodeErrors) == len(h.donConfig.Members) && len(nodeErrors) > 0 {
		return h.sendResponse(ctx, ar, h.errorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes"), nil))
	}

	l.Debugw("successfully forwarded request to Vault nodes")
	return nil
}
```
