Confirmed: no per-sender or global rate limiting exists on the incoming legacy user-message path before it reaches `HandleLegacyUserMessage` — only `MaxRequestBytesLimiter` (payload size) is enforced at the HTTP layer, and the `capabilities` handler's `HandleLegacyUserMessage` has no user rate limiter/allowlist at all (unlike the `functions` handler which uses `userRateLimiter`/`allowlist`).

### Title
Unbounded `savedCallbacks` insertion via unauthenticated `HandleLegacyUserMessage` allows attacker-triggered eviction of legitimate pending callbacks in `pruneCallbacks`, causing dropped DON responses - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`HandleLegacyUserMessage` stores every incoming user request in the shared `h.savedCallbacks` map keyed by attacker-controlled `MessageId`, with no allowlist or per-sender/global rate limiting applied (explicitly marked by a `TODO` comment) [1](#0-0) . The periodic `pruneCallbacks` job only bounds the map size after the fact by globally sorting all entries by `createdAt` and deleting the oldest half once `len(savedCallbacks) > MaxSavedCallbacks` [2](#0-1) , meaning a flood of attacker requests within one `CallbackPruneIntervalSec` window can force eviction of unrelated, still-pending legitimate callbacks before their DON response arrives.

### Finding Description
`HandleLegacyUserMessage` validates payload structure/staleness/method but performs no sender allowlist check and no per-user/global rate limiting before writing to the shared map: [3](#0-2) . This is reachable directly from the gateway's public HTTP endpoint via `gateway.ProcessRequest`, which routes any legacy-format message (identified by a non-empty `DonId`) straight to `h.HandleLegacyUserMessage` after only basic JSON-RPC/message-ID-length validation [4](#0-3) . No authentication token is required on this path.

Because `MessageId` is attacker-supplied and used as the map key, an attacker can generate an arbitrarily large number of distinct, well-formed legacy trigger messages and submit them in rapid succession. Each call inserts a new `*savedCallback` with `createdAt = time.Now()` [5](#0-4) . The only capacity control is the background pruning goroutine that runs once every `CallbackPruneIntervalSec` (default 30s) [6](#0-5) . Within that 30-second window, if the total map size exceeds `MaxSavedCallbacks` (default 20000), `pruneCallbacks` sorts **all** entries — attacker's and legitimate users' alike — by `createdAt` and deletes the oldest half, irrespective of ownership or remaining time-to-live relative to `CallbackMaxAgeSec` (default 120s) [7](#0-6) . A legitimate request submitted moments before the attack burst, and still awaiting a DON response, is among the "oldest" and gets deleted from the map even though it has not expired.

When the DON later responds, `handleWebAPITriggerMessage` looks up the callback by `MessageId`; if it was pruned, `found` is `false` and the response is silently dropped with no error surfaced to the node or to the original caller [8](#0-7) . The original caller's `callback.Wait(ctx)` in `gateway.ProcessRequest` will simply time out and return a `RequestTimeoutError` to the legitimate user [9](#0-8) , with the true response from the DON discarded. Contrast this to the `functions` handler, which enforces both an `allowlist.Allow(sender)` check and a `userRateLimiter.Allow(msg.Body.Sender)` check before ever touching its pending-request cache [10](#0-9)  — protections that are explicitly absent from `capabilities.handler.HandleLegacyUserMessage`.

The only remaining barrier is the HTTP server's `MaxRequestBytesLimiter`, which limits body size per request, not request rate or count, so it does not prevent this flood [11](#0-10) .

### Impact Explanation
This is a capability-routing denial-of-service: an unprivileged, unauthenticated attacker can cause legitimate, still-pending web API trigger/target/compute-action/workflow-syncer callbacks belonging to other users to be silently evicted, so their DON responses are dropped and callers see spurious timeouts instead of correct results. This matches the "misreporting due to silently dropped legitimate responses" / gateway DoS impact class — legitimate requests fail to receive their real DON-computed answer even though the DON processed them correctly.

### Likelihood Explanation
Feasible and repeatable: the attacker needs no credentials, no allowlist membership, and no signing key trusted by any node — only the ability to POST distinct, structurally valid legacy JSON-RPC messages to the gateway's public endpoint (`MethodWebAPITrigger`) with a valid non-stale timestamp and a well-formed `TriggerRequestPayload`. To guarantee eviction, the attacker must push the map above `MaxSavedCallbacks` (default 20000) within a single `CallbackPruneIntervalSec` window (default 30s), i.e., roughly ~667+ req/s sustained, which is achievable from a small number of concurrent HTTP clients since there is no per-sender or global limiter on this path. The attack can be repeated every prune cycle to sustain the DoS.

### Recommendation
Apply the same protections used in the `functions` handler: enforce a sender allowlist and a per-sender/global `ratelimit.RateLimiter` (or `nodeRateLimiter`-equivalent for users) inside `HandleLegacyUserMessage` before inserting into `savedCallbacks`, reject/queue requests once a per-sender or global in-flight callback quota is reached, and change `pruneCallbacks` eviction to be sender-aware (e.g., cap per-sender entries, or refuse new insertions rather than evicting others) so that one sender's flood cannot evict another sender's pending callback.

### Proof of Concept
Unit/integration test plan (extending `handler_test.go`):
1. Construct a `handler` with `MaxSavedCallbacks` set to a small test value (e.g., 10) and `CallbackPruneIntervalSec` short (e.g., 1s).
2. Insert one legitimate `savedCallback` via `HandleLegacyUserMessage` with a valid signed message and capture its callback.
3. In a tight loop, call `HandleLegacyUserMessage` with 100+ distinct `MessageId`s (simulating attacker), all created immediately after the legitimate entry, pushing `len(h.savedCallbacks)` above `MaxSavedCallbacks`.
4. Invoke `handler.pruneCallbacks()` directly (or wait for the ticker).
5. Assert that the legitimate callback's `MessageId` is no longer present in `h.savedCallbacks` (`require.NotContains`) even though `now.Sub(createdAt) < CallbackMaxAgeSec`.
6. Simulate the DON response for the legitimate `MessageId` via `handleWebAPITriggerMessage`/`HandleNodeMessage` and assert `found == false` and that the legitimate caller's `callback.Wait` never receives `SendResponse`, demonstrating the dropped response.

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L148-162)
```go
func (h *handler) handleWebAPITriggerMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.mu.Lock()
	savedCb, found := h.savedCallbacks[msg.Body.MessageId]
	delete(h.savedCallbacks, msg.Body.MessageId)
	h.mu.Unlock()

	if found {
		// Send first response from a node back to the user, ignore any other ones.
		// TODO: in practice, we should wait for at least 2F+1 nodes to respond and then return an aggregated response
		// back to the user.
		codec := api.JsonRPCCodec{}
		return savedCb.SendResponse(handlers.UserCallbackPayload{RawResponse: codec.EncodeLegacyResponse(msg), ErrorCode: api.NoError})
	}
	return nil
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L303-334)
```go
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-384)
```go
	// TODO: apply allowlist and rate-limiting here
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L411-414)
```go
	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()
```

**File:** core/services/gateway/gateway.go (L250-269)
```go
	} else {
		// Legacy request with DON ID - validate and fetch handler
		isLegacyRequest = true
		if err = msg.Validate(); err != nil {
			return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
		}
		handlerKey = msg.Body.DonId
		var ok bool
		h, ok = g.handlers[handlerKey]
		if !ok {
			return newError(jsonRequest.ID, api.UnsupportedDONIdError, "Unsupported DON ID: "+handlerKey)
		}
	}

	startTime := time.Now()
	var method string
	callback := handlerscommon.NewCallback()
	if isLegacyRequest {
		method = msg.Body.Method
		err = h.HandleLegacyUserMessage(ctx, msg, callback)
```

**File:** core/services/gateway/gateway.go (L278-285)
```go
	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
```

**File:** core/services/gateway/handlers/functions/handler.functions.go (L208-219)
```go
func (h *functionsHandler) HandleLegacyUserMessage(ctx context.Context, msg *api.Message, callback handlers.Callback) error {
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

**File:** core/services/gateway/network/httpserver.go (L36-60)
```go
type HTTPServerConfig struct {
	Host                   string
	Port                   uint16
	TLSEnabled             bool
	TLSCertPath            string
	TLSKeyPath             string
	Path                   string
	ContentTypeHeader      string
	ReadTimeoutMillis      uint32
	WriteTimeoutMillis     uint32
	RequestTimeoutMillis   uint32
	MaxRequestBytes        int64
	MaxRequestBytesLimiter limits.BoundLimiter[config.Size] // supersedes MaxRequestBytes, if set
	CORSEnabled            bool
	CORSAllowedOrigins     []string
}

func (c *HTTPServerConfig) ensureLimiters(lf limits.Factory) (err error) {
	if c.MaxRequestBytesLimiter == nil {
		limit := cresettings.Default.GatewayIncomingPayloadSizeLimit
		limit.DefaultValue = config.Size(c.MaxRequestBytes)
		c.MaxRequestBytesLimiter, err = limits.MakeBoundLimiter(lf, limit)
	}
	return
}
```
