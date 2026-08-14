### Title
Unauthenticated DoS via unbounded fan-out in `HandleLegacyUserMessage` - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` is reachable from any external caller through the gateway's user-facing HTTP endpoint with no allowlist or rate-limiting on the request path, as explicitly marked by a `TODO` comment in the code. Each accepted message triggers a fan-out `don.SendToNode` call to every member of `h.donConfig.Members`, so an attacker can multiply a single HTTP request into N backend calls to all DON nodes, repeated arbitrarily.

### Finding Description
The gateway's HTTP server accepts unauthenticated user requests at `network.httpServer.handleRequest`, which forwards the raw body and an optional bearer token to `gateway.ProcessRequest` [1](#0-0) . For legacy requests (identified by a populated `DonId`), `ProcessRequest` only calls `msg.Validate()` (structural/basic checks) and then routes to `h.HandleLegacyUserMessage(ctx, msg, callback)` [2](#0-1) .

Inside `HandleLegacyUserMessage`, the checks performed are: payload decode, `payload.Timestamp != 0`, and a staleness check against `MaxAllowedMessageAgeSec` [3](#0-2) . None of these prevent an attacker from submitting an unbounded number of distinct, freshly-timestamped, uniquely-`MessageId`d requests. Immediately after these checks, the code contains the explicit marker:

```go
// TODO: apply allowlist and rate-limiting here
```

followed directly by fan-out to every DON member via `don.SendToNode` [4](#0-3) . The only rate limiter present in this handler, `h.nodeRateLimiter`, is applied solely on the node→gateway path in `handleWebAPIOutgoingMessage` [5](#0-4) , and is never consulted for user→gateway traffic. There is no incoming-message rate limiter, IP/sender allowlist, or per-caller quota checked before the fan-out loop.

### Impact Explanation
Because each legacy user message multiplies into `len(h.donConfig.Members)` outbound calls to `don.SendToNode`, an unauthenticated caller can force the gateway to hammer every node in the DON with attacker-controlled request volume, exhausting DON node processing/connection capacity and gateway resources (goroutines, saved callback map entries via `h.savedCallbacks`). This is a DON-wide denial-of-service originating from a single unprivileged external actor, degrading availability of legitimate workflow trigger/target processing across the DON.

### Likelihood Explanation
The precondition is only network access to the gateway's user-facing HTTP endpoint (`UserServerConfig` port), with no external allowlist configured — which matches the default/undeveloped state indicated by the `TODO`. The attack requires no authentication, no valid node key, and no privileged role: an attacker simply POSTs distinct JSON-RPC legacy messages with unique `MessageId`/fresh `Timestamp` values. This is trivially scriptable and repeatable at high rate, limited only by the gateway's generic HTTP body-size limiter (`MaxRequestBytesLimiter`), which does not throttle request frequency.

### Recommendation
Implement the still-missing allowlist and rate-limiting at the marked `TODO` location in `HandleLegacyUserMessage`, before the fan-out loop: add an incoming user-message rate limiter (e.g., keyed by source IP/auth token, analogous to `h.nodeRateLimiter`) and an allowlist check against expected/authorized senders, rejecting or throttling requests that exceed configured thresholds prior to invoking `don.SendToNode` for DON members.

### Proof of Concept
Integration test plan:
1. Instantiate a `handler` (as in `handler_test.go`) with a `donConfig` containing multiple mock DON members and a mock `handlers.DON` whose `SendToNode` increments an atomic counter.
2. In a loop, call `h.HandleLegacyUserMessage(ctx, msg, callback)` N times (e.g., N=10,000) with distinct `MessageId` values and valid/fresh `Timestamp`s, each wrapped in a no-op `Callback`.
3. Assert that `SendToNode` invocation count equals `N * len(donConfig.Members)` with no request being rejected/throttled, and that this exceeds any reasonable safe bound for DON node capacity (e.g., far above a target QPS-per-node threshold), demonstrating the absence of enforced rate limiting on this path.
4. Optionally measure wall-clock time and goroutine/memory growth (via `h.savedCallbacks` size and `h.wg`) to show resource exhaustion trending unbounded with N.

### Citations

**File:** core/services/gateway/network/httpserver.go (L211-219)
```go
	// Optionally extract jwt token from authorization header
	authHeader := r.Header.Get("Authorization")
	jwtToken := ""
	if authHeader != "" {
		jwtToken = strings.TrimPrefix(authHeader, "Bearer ")
	}

	startTime := time.Now()
	rawResponse, httpStatusCode := s.handler.ProcessRequest(r.Context(), rawMessage, jwtToken)
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

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-383)
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
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L384-420)
```go
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
	req, err := common.ValidatedRequestFromMessage(msg)
	if err != nil {
		h.lggr.Errorw(ErrTransformingMessageToRequest)
		return callback.SendResponse(handlers.UserCallbackPayload{
			RawResponse: codec.EncodeNewErrorResponse(
				msg.Body.MessageId,
				api.ToJSONRPCErrorCode(api.UserMessageParseError),
				ErrTransformingMessageToRequest,
				nil,
			),
			ErrorCode: api.UserMessageParseError,
		})
	}

	h.mu.Lock()
	h.savedCallbacks[msg.Body.MessageId] = &savedCallback{id: msg.Body.MessageId, createdAt: time.Now(), Callback: callback}
	don := h.don
	h.mu.Unlock()

	// Send original request to all nodes
	for _, member := range h.donConfig.Members {
		err = errors.Join(err, don.SendToNode(ctx, member.Address, req))
	}
	return err
```
