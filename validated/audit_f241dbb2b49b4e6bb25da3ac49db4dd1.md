### Title
Unmetered `secrets_list` requests can exhaust the shared `pendingRequests` cache and deny legitimate secrets requests - ([File: core/services/gateway/handlers/functions/handler.functions.go])

### Finding Description
`functionsHandler.HandleLegacyUserMessage` gates every legacy message with allowlist and rate-limit checks, then only applies a subscription-balance check for `MethodSecretsSet`: [1](#0-0) 

`MethodSecretsList` skips the `h.subscriptions`/`h.minimumBalance` branch entirely (the `if` at line 220 checks `msg.Body.Method == MethodSecretsSet`), so a sender only needs to be allowlisted (or pass no allowlist at all if disabled) to reach `handleRequest`: [2](#0-1) 

`handleRequest` immediately calls `h.pendingRequests.NewRequest`, which inserts a new entry keyed by `{sender, MessageId}` into a single shared map bounded only by `cfg.MaxPendingRequests`: [3](#0-2) [4](#0-3) 

Each entry stays in the cache until either a quorum of node responses arrives or `RequestTimeoutMillis` elapses. The only admission control before insertion is `h.userRateLimiter.Allow(msg.Body.Sender)`, which enforces a per-sender token-bucket **rate** and a **global** token-bucket rate (`ratelimit.RateLimiter`, mirrored by the equivalent limiter shown in `core/services/workflows/ratelimiter/ratelimiter.go`): [5](#0-4) 

Critically, nothing bounds the number of *concurrently pending* (in-flight, unresolved) requests per sender — only the arrival *rate* is limited. Because each unique `MessageId` produces a distinct cache key even for the same sender, an attacker can keep sending fresh `MessageId` values for `MethodSecretsList` at a rate within its allowed RPS/burst, and each accepted request occupies a slot in the shared cache for up to `RequestTimeoutMillis`. If `GlobalRPS * (RequestTimeoutMillis/1000)` (or `PerSenderRPS * (RequestTimeoutMillis/1000)` for a single sender) exceeds `MaxPendingRequests`, the shared cache fills up, and subsequent legitimate `NewRequest` calls (for both `secrets_set` and `secrets_list`, from any sender) fail with `"request cache is full"`: [6](#0-5) 

Since `secrets_list` has no balance gate, this can be done by an allowlisted address with zero subscription balance — no on-chain funding required, only allowlist membership (which, per the allowlist implementation, can include arbitrarily many addresses synced from an on-chain ToS contract): [7](#0-6) 

If the allowlist is large/permissive or disabled, address rotation across many allowlisted senders further defeats the per-sender rate limiter bucket (each new sender gets a fresh bucket), leaving only the shared `GlobalRPS`/`GlobalBurst` ceiling as protection — which still does not bound cache *occupancy* relative to `RequestTimeoutMillis`.

### Impact Explanation
This is a resource-exhaustion / denial-of-service issue against the Functions Gateway's secrets-set/secrets-list pipeline: an unprivileged, low-cost (zero-balance) allowlisted sender can starve the shared `pendingRequests` cache, causing legitimate users' `secrets_set`/`secrets_list` requests to be rejected with `"request cache is full"`. This matches the "denial of secret-retrieval / DoS as a stepping stone to secret staleness" impact category — it does not itself leak secrets or forge transactions, but it degrades availability of the DON secrets subsystem for legitimate off-chain requests.

### Likelihood Explanation
Feasibility depends on gateway configuration: it requires (a) the attacker being allowlisted (trivial if the on-chain ToS allowlist is large/open or the allowlist is disabled), and (b) `MaxPendingRequests` being small relative to `GlobalRPS`/`PerSenderRPS` × `RequestTimeoutMillis`. There is no code-level correlation/validation enforcing `MaxPendingRequests >= RPS * timeout`, so misconfiguration (or even a modestly generous rate limiter combined with a multi-second timeout) makes this practically reachable. The attack is repeatable indefinitely as long as the attacker maintains its allowed request rate, and is exactly as effective for `secrets_list` (unmetered) as `secrets_set` (balance-gated), making `secrets_list` the cheaper vector.

### Recommendation
- Add a per-sender cap on concurrently pending requests within `RequestCache` (in addition to the existing rate limiter), independent of `MaxPendingRequests`'s global bound, e.g., track and enforce `maxPendingPerSender` in `requestCache.NewRequest`.
- Size/validate `MaxPendingRequests` relative to `UserRateLimiter` config and `RequestTimeoutMillis` at startup (reject or warn on configurations where `GlobalRPS * timeoutSeconds` can exceed `MaxPendingRequests`).
- Consider applying a minimum-balance/cost gate (or a stricter, lower per-sender rate limit) to `MethodSecretsList` as well, since it is currently the only method exempt from the balance check while still consuming the same shared cache resource as `MethodSecretsSet`.

### Proof of Concept
Unit/integration test plan in `core/services/gateway/handlers/functions/handler.functions_test.go`:
1. Construct a `functionsHandler` with a small `MaxPendingRequests` (e.g., 5), a generous `UserRateLimiter` (e.g., `PerSenderRPS: 100, PerSenderBurst: 100`), a long `RequestTimeoutMillis` (e.g., 60000), an allowlist that allows one attacker address, and `subscriptions`/`minimumBalance` configured (attacker has zero balance).
2. From the single allowlisted attacker address, send `MaxPendingRequests` distinct `MethodSecretsList` messages with unique `MessageId`s via `HandleLegacyUserMessage`.
3. Assert all succeed (no error), and that `h.pendingRequests` internal size equals `MaxPendingRequests` (or observe via a subsequent call).
4. Send one additional `MethodSecretsList` (or `MethodSecretsSet`) message from a different, legitimate sender with sufficient balance.
5. Assert the call returns `"request cache is full"`, demonstrating that a zero-balance attacker using only `secrets_list` denies service to a legitimate, balance-holding `secrets_set`/`secrets_list` requester — before any of the attacker's requests time out.

### Citations

**File:** core/services/gateway/handlers/functions/handler.functions.go (L208-232)
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
	if msg.Body.Method == MethodSecretsSet && h.subscriptions != nil && h.minimumBalance != nil {
		balance, err := h.subscriptions.GetMaxUserBalance(sender)
		if err != nil {
			h.lggr.Debugw("error getting max user balance", "sender", msg.Body.Sender, "err", err)
		}
		if balance == nil {
			balance = big.NewInt(0)
		}
		if err != nil || balance.Cmp(h.minimumBalance.ToInt()) < 0 {
			h.lggr.Debugw("received a message from a user having insufficient balance", "sender", msg.Body.Sender, "balance", balance.String())
			return fmt.Errorf("sender has insufficient balance: %v juels", balance.String())
		}
	}
```

**File:** core/services/gateway/handlers/functions/handler.functions.go (L233-248)
```go
	switch msg.Body.Method {
	case MethodSecretsSet, MethodSecretsList:
		return h.handleRequest(ctx, msg, callback)
	case MethodHeartbeat:
		if _, ok := h.allowedHeartbeatInitiators[msg.Body.Sender]; !ok {
			h.lggr.Debugw("received heartbeat request from a non-allowed sender", "sender", msg.Body.Sender)
			promHandlerError.WithLabelValues(h.donConfig.DonId, ErrNotAllowlisted.Error()).Inc()
			return ErrUnsupportedMethod
		}
		return h.handleRequest(ctx, msg, callback)
	default:
		h.lggr.Debugw("unsupported method", "method", msg.Body.Method)
		promHandlerError.WithLabelValues(h.donConfig.DonId, ErrUnsupportedMethod.Error()).Inc()
		return ErrUnsupportedMethod
	}
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

**File:** core/services/workflows/ratelimiter/ratelimiter.go (L40-52)
```go
func (rl *RateLimiter) Allow(sender string) (senderAllow bool, globalAllow bool) {
	rl.mu.Lock()
	senderLimiter, ok := rl.perSender[sender]
	if !ok {
		senderLimiter = rate.NewLimiter(rate.Limit(rl.config.PerSenderRPS), rl.config.PerSenderBurst)
		rl.perSender[sender] = senderLimiter
	}
	rl.mu.Unlock()

	senderAllow = senderLimiter.Allow()
	globalAllow = rl.global.Allow()
	return senderAllow, globalAllow
}
```

**File:** core/services/gateway/handlers/functions/allowlist/allowlist.go (L175-179)
```go
func (a *onchainAllowlist) Allow(address common.Address) bool {
	allowlist := *a.allowlist.Load()
	_, ok := allowlist[address]
	return ok
}
```
