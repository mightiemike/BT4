### Title
Unbounded `h.savedCallbacks` growth via unthrottled `HandleLegacyUserMessage` inserts enables gateway memory-exhaustion DoS - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` inserts a new entry into `h.savedCallbacks` for every distinct `MessageId` with no admission check against `MaxSavedCallbacks`, and the map is only trimmed periodically by `pruneCallbacks` on a `CallbackPruneIntervalSec` ticker (default 30s). An unprivileged caller who can reach this handler with many distinct `MessageId`s can grow the map far beyond the configured bound between prune cycles, causing gateway memory exhaustion.

### Finding Description
In `HandleLegacyUserMessage` [1](#0-0) , after basic payload/timestamp/method validation, the handler unconditionally does:
```go
h.mu.Lock()
h.savedCallbacks[msg.Body.MessageId] = &savedCallback{...}
h.mu.Unlock()
```
There is no check of `len(h.savedCallbacks)` against `h.config.MaxSavedCallbacks` at insertion time. The only bound enforcement is in `pruneCallbacks` [2](#0-1) , which runs only when the ticker fires, on the interval configured by `CallbackPruneIntervalSec` (default `defaultCallbackPruneIntervalSec = 30` seconds) [3](#0-2) .

The code comment itself acknowledges this gap: `defaultMaxSavedCallbacks = 20000 // could briefly exceed under heavy load` [4](#0-3) . Additionally, there is an explicit TODO left in the validation path noting the absence of rate limiting for this exact entry point: `// TODO: apply allowlist and rate-limiting here` immediately preceding the method check in `HandleLegacyUserMessage` [5](#0-4) . The only other checks performed before insertion are payload decoding, a non-zero timestamp check, and a message-age/staleness check (`MaxAllowedMessageAgeSec`) [6](#0-5)  — none of which bound the number of distinct concurrently-tracked callbacks, and none of which rate-limit a single sender submitting many distinct `MessageId` values within the message-age window.

The `nodeRateLimiter` field (`ratelimit.RateLimiter`) that exists on the handler is only applied in `handleWebAPIOutgoingMessage`, which handles node-to-gateway traffic keyed by `nodeAddr`, not in the user-facing `HandleLegacyUserMessage` path [7](#0-6) . Thus an unprivileged HTTP caller reaching this handler through the gateway's user-message ingress can freely submit distinct valid-looking `MethodWebAPITrigger` messages without any per-sender throttling or map-size admission check.

### Impact Explanation
Each accepted message adds one `*savedCallback` entry (holding an `id`, `createdAt`, and a `handlers.Callback` closure) to `h.savedCallbacks` and is retained until it is either fulfilled by a matching node response, expires, or is evicted by the periodic prune. If the attacker's request rate exceeds the drain rate achievable within a 30-second prune window, the map grows unbounded between prune cycles, consuming heap memory proportional to attacker-controlled input volume. This is a gateway-side memory-exhaustion Denial of Service reachable from a single unprivileged sender, matching the "resource exhaustion / DoS of a Chainlink service" bounty impact category — it does not grant privilege escalation or fund/data compromise, but can degrade or crash the gateway process serving the DON.

### Likelihood Explanation
Feasibility depends on whether an unprivileged client can generate enough valid `HandleLegacyUserMessage` calls (with distinct `MessageId`s, non-zero timestamp, and a valid `web_api_trigger` method, passing `common.ValidatedRequestFromMessage`) within a 30-second window to exceed `defaultMaxSavedCallbacks` (20000) before pruning trims the map. The exact signature/validation requirements enforced by `common.ValidatedRequestFromMessage` and the outer gateway ingress path (HTTP handler, potential connection-level throttling) were not fully confirmed in this session — I was not able to fully verify whether an upstream HTTP-layer rate limiter or signature-verification cost imposes a practical throughput ceiling on this call path before reaching `HandleLegacyUserMessage`. Given the code comment explicitly acknowledging "could briefly exceed under heavy load" and the TODO marking the absence of rate-limiting on this exact path, the underlying design gap is confirmed by the maintainers' own comments, but the concrete achievable request rate (and thus real-world severity) is unverified without load-testing the full ingress stack.

### Recommendation
Add an admission check in `HandleLegacyUserMessage` before inserting into `h.savedCallbacks`: if `len(h.savedCallbacks) >= h.config.MaxSavedCallbacks` (checked under `h.mu`), reject the message with a backpressure/error response (e.g., a new `HandlerError` variant like "too many pending callbacks") instead of unconditionally inserting. Additionally, apply the existing `nodeRateLimiter`-style per-sender rate limiting to the `HandleLegacyUserMessage` path (the TODO already flags this), and consider making `pruneCallbacks` also invokable synchronously/opportunistically (e.g., trigger a prune pass when the map size crosses a high-water mark) rather than relying solely on the fixed ticker interval.

### Proof of Concept
Integration/load test plan for `core/services/gateway/handlers/capabilities/handler_test.go`:
1. Construct a `handler` via `NewHandler` with `HandlerConfig{MaxSavedCallbacks: N, CallbackPruneIntervalSec: 30}` and a mock `handlers.DON`/`network.HTTPClient`.
2. In a tight loop (no sleep), call `HandleLegacyUserMessage` with `N*3` distinct `MessageId`s, each a validly-formed `web_api_trigger` message with a fresh `Timestamp` and passing `common.ValidatedRequestFromMessage`, using a no-op `handlers.Callback`.
3. Immediately after the loop (before the 30s ticker fires), assert `len(h.savedCallbacks) > h.config.MaxSavedCallbacks` (e.g., close to `N*3`), demonstrating no admission-time bound.
4. Optionally wait for one `CallbackPruneIntervalSec` tick and assert the map is trimmed to `~MaxSavedCallbacks/2`, confirming pruning is the only enforcement mechanism and occurs only after the fact.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L43-45)
```go
	defaultCallbackMaxAgeSec        = 120   // 2 minutes
	defaultMaxSavedCallbacks        = 20000 // could briefly exceed under heavy load
	defaultCallbackPruneIntervalSec = 30
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L269-285)
```go
func (h *handler) Start(context.Context) error {
	return h.StartOnce(handlerName, func() error {
		h.wg.Go(func() {
			ticker := time.NewTicker(time.Duration(h.config.CallbackPruneIntervalSec) * time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					h.pruneCallbacks()
				case <-h.stopCh:
					return
				}
			}
		})
		return nil
	})
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L299-339)
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

	// If there are still too many callbacks, sort them by creation time and remove the oldest ones.
	maxSize := h.config.MaxSavedCallbacks
	var evicted int
	if len(h.savedCallbacks) > maxSize {
		type entry struct {
			id        string
			createdAt time.Time
		}
		entries := make([]entry, 0, len(h.savedCallbacks))
		for id, cb := range h.savedCallbacks {
			entries = append(entries, entry{id, cb.createdAt})
		}
		sort.Slice(entries, func(i, j int) bool {
			return entries[i].createdAt.Before(entries[j].createdAt)
		})
		// Trim to maxSize/2 to avoid sorting the list too frequently.
		for _, e := range entries[:len(entries)-maxSize/2] {
			delete(h.savedCallbacks, e.id)
			evicted++
		}
	}

	if expired > 0 || evicted > 0 {
		h.lggr.Infow("Pruned savedCallbacks", "expired", expired, "evicted", evicted, "remaining", len(h.savedCallbacks))
	}
}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-396)
```go
func (h *handler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
	body := msg.Body
	var payload webapicap.TriggerRequestPayload
	codec := api.JsonRPCCodec{}
	err := json.Unmarshal(body.Payload, &payload)
	if err != nil {
		h.lggr.Errorw(ErrDecodingPayload, "err", err)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload+" "+err.Error(),
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if payload.Timestamp == 0 {
		h.lggr.Errorw(ErrDecodingPayload)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrDecodingPayload,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	if uint(time.Now().Unix())-h.config.MaxAllowedMessageAgeSec > uint(payload.Timestamp) {
		h.lggr.Errorw("stale message")
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.HandlerError),
				"stale message",
				nil,
			),
			ErrorCode: api.HandlerError,
		})
	}
	// TODO: apply allowlist and rate-limiting here
	if msg.Body.Method != MethodWebAPITrigger {
		h.lggr.Errorw("unsupported method", "method", body.Method)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UnsupportedMethodError),
				"invalid method "+msg.Body.Method,
				nil,
			),
			ErrorCode: api.UnsupportedMethodError,
		})
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```
