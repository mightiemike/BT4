### Title
Global unbounded-until-timeout `RequestCache` in Functions gateway handler enables single-sender pending-request-cache exhaustion DoS - ([File: core/services/gateway/handlers/functions/handler.functions.go])

### Summary
The Functions gateway handler stores all pending user requests in a single, DON-wide `hc.RequestCache` with a fixed `MaxPendingRequests` capacity and no per-sender quota. Any single allowlisted/rate-limit-passing sender can fill the shared cache with unique `MessageId` requests up to `MaxPendingRequests`, causing all subsequent legitimate senders to receive `"request cache is full"` errors until `RequestTimeoutMillis` elapses and entries expire.

### Finding Description
`functionsHandler.handleRequest` calls `h.pendingRequests.NewRequest(...)` for every accepted `MethodSecretsSet`/`MethodSecretsList`/`MethodHeartbeat` message [1](#0-0) . The underlying `requestCache.NewRequest` implementation keys entries by `{sender, messageId}` and enforces only a single global `maxCacheSize` bound shared across all senders — there is no per-sender limit: [2](#0-1) .

Because the key includes `messageId`, an attacker-controlled, per-request value chosen by the sender, a single sender that passes the allowlist check (`h.allowlist.Allow(sender)`) and the per-sender `userRateLimiter.Allow(msg.Body.Sender)` check [3](#0-2)  can generate arbitrarily many distinct cache entries by varying `MessageId`, as long as the aggregate rate stays within whatever `userRateLimiter` allows over the `RequestTimeoutMillis` window (rate limiting bounds throughput per sender, not the total number of concurrently pending entries system-wide).

Each entry remains in the cache until either (a) enough DON node responses arrive to satisfy `processSecretsResponse`/`processHeartbeatResponse` aggregation thresholds (F+1 successes or N-F failures), or (b) the per-entry `time.AfterFunc` timeout fires after `RequestTimeoutMillis` [4](#0-3) . Once `len(c.cache) >= maxCacheSize`, `NewRequest` unconditionally returns `"request cache is full"` for every sender, including legitimate/other users, regardless of their own standing [5](#0-4) .

None of the existing checks (allowlist, per-sender rate limiter, subscription balance check) constrain the *number of concurrently pending* requests per sender — they only gate admission of individual messages. There is no mechanism ensuring fairness of the shared, fixed-size cache across senders.

### Impact Explanation
This is an availability/DoS issue against the Functions Gateway: while the cache is saturated, all senders (not just the attacker) are denied service for `secrets_set`, `secrets_list`, and heartbeat requests routed through this DON, since `NewRequest` fails identically for every caller once the global cap is reached. This matches a scoped "denial of service on gateway/capability handler" impact rather than a fund-loss or key-compromise bug, but it is a real service-disruption vector reachable via ordinary allowlisted gateway API traffic.

### Likelihood Explanation
Preconditions: attacker must be an allowlisted sender that passes `userRateLimiter` (the question's stated precondition). Given that, the attack is straightforward and repeatable: continuously issue requests with unique `MessageId`s at a rate within the rate limiter's allowance, sustained long enough to accumulate `MaxPendingRequests` entries before existing ones are resolved/time out. Typical `MaxPendingRequests`/`RequestTimeoutMillis` configuration values determine feasibility, but the mechanism has no inherent per-sender cap to prevent it, so it is generically exploitable by design.

### Recommendation
Add a per-sender quota on concurrently pending entries in `requestCache` (e.g., cap outstanding entries per `sender` independent of the global `maxCacheSize`), or partition/reserve cache capacity per sender/allowlist tier so that one sender cannot monopolize the shared `MaxPendingRequests` budget. Alternatively, reject/evict oldest entries per sender when a sender's own pending count exceeds a configurable threshold before consulting the global cap.

### Proof of Concept
Unit test in `core/services/gateway/handlers/common/requestcache_test.go` or a new test in the `functions` package:
1. Construct `hc.NewRequestCache[PendingRequest](timeout, maxCacheSize=N)` with small `N` (e.g. 3) and a moderate `timeout` (e.g. 500ms).
2. From `senderA`, call `NewRequest` N times with distinct `MessageId`s and a callback that never gets a matching `ProcessResponse` call (simulating "never receive DON responses"). Assert all N calls succeed.
3. From `senderB` (a different sender), call `NewRequest` with a fresh `MessageId`. Assert it returns error `"request cache is full"`.
4. Wait until just after `timeout` elapses (letting the timers fire and evict `senderA`'s entries).
5. Retry `senderB`'s `NewRequest` call and assert it now succeeds, confirming the DoS window lasted exactly `RequestTimeoutMillis` and was caused solely by `senderA`'s unresolved entries occupying the shared cache.

### Citations

**File:** core/services/gateway/handlers/functions/handler.functions.go (L209-219)
```go
	sender := common.HexToAddress(msg.Body.Sender)
	if h.allowlist != nil && !h.allowlist.Allow(sender) {
		h.lggr.Debugw("received a message from a non-allowlisted address", "sender", msg.Body.Sender)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrNotAllowlisted.Error()).Inc()
		return ErrNotAllowlisted
	}
	if h.userRateLimiter != nil && !h.userRateLimiter.Allow(msg.Body.Sender) {
		h.lggr.Debugw("rate-limited", "sender", msg.Body.Sender)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrRateLimited.Error()).Inc()
		return ErrRateLimited
	}
```

**File:** core/services/gateway/handlers/functions/handler.functions.go (L250-257)
```go
func (h *functionsHandler) handleRequest(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	h.lggr.Debugw("handleRequest: processing message", "sender", msg.Body.Sender, "messageId", msg.Body.MessageId)
	err := h.pendingRequests.NewRequest(h.lggr, msg, callback, &PendingRequest{request: msg, responses: make(map[string]*api.Message)})
	if err != nil {
		h.lggr.Warnw("handleRequest: error adding new request", "sender", msg.Body.Sender, "err", err)
		promHandlerError.WithLabelValues(h.donConfig.DonId, err.Error()).Inc()
		return err
	}
```

**File:** core/services/gateway/handlers/common/requestcache.go (L50-76)
```go
func (c *requestCache[T]) NewRequest(lggr logger.Logger, request *api.Message, callback handlers.Callback, responseData *T) error {
	if request == nil {
		return errors.New("request is nil")
	}
	if responseData == nil {
		return errors.New("responseData is nil")
	}
	key := globalId{request.Body.Sender, request.Body.MessageId}
	c.mu.Lock()
	defer c.mu.Unlock()
	_, ok := c.cache[key]
	if ok {
		return errors.New("request already exists")
	}
	if len(c.cache) >= int(c.maxCacheSize) {
		return errors.New("request cache is full")
	}
	codec := api.JsonRPCCodec{}
	timer := time.AfterFunc(c.timeout, func() {
		err := c.deleteAndSendOnce(key, handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(request), ErrorCode: api.RequestTimeoutError})
		if err != nil {
			lggr.Errorw("failed to send timeout response", "error", err)
		}
	})
	c.cache[key] = &pendingRequest[T]{Callback: callback, responseData: responseData, timeoutTimer: timer}
	return nil
}
```
