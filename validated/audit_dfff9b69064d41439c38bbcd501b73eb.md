### Title
Unauthenticated fan-out DoS via unrate-limited `HandleLegacyUserMessage` in the legacy capabilities gateway handler - ([File: core/services/gateway/handlers/capabilities/handler.go])

### Summary
`handler.HandleLegacyUserMessage` accepts external user messages via the gateway's HTTP endpoint and, after only trivial payload/timestamp checks, fans each request out to every DON member via `don.SendToNode` with no allowlist or rate limit applied, as explicitly flagged by the `TODO: apply allowlist and rate-limiting here` comment. The only rate limiter present on the `handler` struct (`h.nodeRateLimiter`) is applied in `handleWebAPIOutgoingMessage`, which throttles node→gateway traffic, not incoming user requests.

### Finding Description
The call path is: `network/httpserver.go` `handleRequest` → `gateway.go` `ProcessRequest` → `handler.HandleLegacyUserMessage` (`core/services/gateway/handlers/capabilities/handler.go:341-421`). Looking at `ProcessRequest` (`core/services/gateway/gateway.go:218-292`), the only gating before dispatch is decoding, an ID length check (`len(jsonRequest.ID) > 200`), and `msg.Validate()` for legacy requests — none of these are rate limits or sender allowlists. Inside `HandleLegacyUserMessage`, the checks performed are: payload unmarshal success, `payload.Timestamp != 0`, and message staleness (`core/services/gateway/handlers/capabilities/handler.go:359-383`). Immediately after, the code hits the marked `// TODO: apply allowlist and rate-limiting here` (line 384) and then loops over `h.donConfig.Members`, calling `don.SendToNode` for each (lines 416-419) with no throttling of the incoming message itself.

This contrasts directly with the sibling `functionsHandler.HandleLegacyUserMessage` (`core/services/gateway/handlers/functions/handler.functions.go:208-219`), which explicitly checks `h.allowlist.Allow(sender)` and `h.userRateLimiter.Allow(msg.Body.Sender)` before doing any fan-out — confirming that per-user allowlisting/rate-limiting is the established pattern elsewhere in this same package family, and its absence here is a real gap rather than a by-design omission.

The message's `MessageId` and `Timestamp` are attacker-controlled fields in the payload, so an attacker can trivially generate unique, non-stale messages to bypass any incidental staleness-based dedup and avoid `savedCallbacks` collisions (line 412: `h.savedCallbacks[msg.Body.MessageId] = ...`), each of which independently triggers `len(h.donConfig.Members)` `SendToNode` calls.

### Impact Explanation
An unauthenticated caller who can reach the gateway's HTTP endpoint can flood `HandleLegacyUserMessage`, producing an unbounded multiplier (N requests × M DON members) of outbound calls into the DON's connection manager (`don.SendToNode`), degrading or exhausting node-processing/connection capacity for the entire DON behind that gateway. This matches a DON-wide denial-of-service impact from a single unprivileged external actor — consistent with the "Denial of Service" bounty tier for infrastructure availability impact, scoped to a single DON's serving capacity, not a network-wide chain halt.

### Likelihood Explanation
High feasibility: no authentication/allowlist is enforced on this legacy path in the capabilities handler, the gateway's HTTP endpoint is user-facing, and the only precondition is "no external allowlist configured" (which the code comment itself confirms is not implemented for this handler). Constructing valid requests only requires unique `MessageId`/`Timestamp` values and a syntactically valid payload — trivially scriptable and repeatable.

### Recommendation
Implement the `TODO` at `core/services/gateway/handlers/capabilities/handler.go:384`: add a per-sender (or per-message-id) rate limiter and/or allowlist check before the fan-out loop, mirroring the pattern already used in `functionsHandler.HandleLegacyUserMessage` (`h.allowlist.Allow(sender)` + `h.userRateLimiter.Allow(msg.Body.Sender)`). Additionally, consider bounding total concurrent in-flight `SendToNode` fan-outs per DON to cap worst-case amplification even if per-sender limits are bypassed via sender/IP rotation.

### Proof of Concept
Integration test plan (extending `core/services/gateway/handlers/capabilities/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a `donConfig` containing M mock DON members and a `handlers.DON` mock (`don.SendToNode`) that records invocation counts per call.
2. In a loop, issue N calls to `HandleLegacyUserMessage` with method `MethodWebAPITrigger`, each with a distinct `MessageId` and a fresh `Timestamp` (within `MaxAllowedMessageAgeSec`), from a single simulated "attacker" sender/no auth context.
3. Assert `don.SendToNode` was invoked at least `N * M` times with no requests rejected for lack of authorization or rate limiting.
4. Assert there is no configurable rate-limit/allowlist rejection path exercised (i.e., confirm current code has 0 rejections regardless of N), demonstrating the missing safeguard.
5. (Optional stress variant) Measure wall-clock/goroutine growth in `h.wg` under sustained load to show unbounded resource consumption proportional to attacker request rate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** core/services/gateway/handlers/capabilities/handler.go (L164-168)
```go
func (h *handler) handleWebAPIOutgoingMessage(ctx context.Context, msg *api.Message, nodeAddr string) error {
	h.lggr.Debugw("handling webAPI outgoing message", "messageId", msg.Body.MessageId, "nodeAddr", nodeAddr)
	if !h.nodeRateLimiter.Allow(nodeAddr) {
		return fmt.Errorf("rate limit exceeded for node %s", nodeAddr)
	}
```

**File:** core/services/gateway/handlers/capabilities/handler.go (L341-421)
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
}
```

**File:** core/services/gateway/gateway.go (L218-292)
```go
func (g *gateway) ProcessRequest(ctx context.Context, rawRequest []byte, auth string) (rawResponse []byte, httpStatusCode int) {
	// decode
	jsonRequest, err := jsonrpc2.DecodeRequest[json.RawMessage](rawRequest, auth)
	if err != nil {
		return newError("", api.UserMessageParseError, err.Error())
	}
	msg, err := g.codec.DecodeJSONRequest(jsonRequest)
	if err != nil {
		return newError(jsonRequest.ID, api.UserMessageParseError, err.Error())
	}
	if len(jsonRequest.ID) > 200 {
		// Arbitrary limit to prevent abuse
		return newError(jsonRequest.ID, api.UserMessageParseError, "request ID is too long: "+strconv.Itoa(len(jsonRequest.ID))+". max is 200 characters")
	}
	var isLegacyRequest = false
	var h handlers.Handler
	var handlerKey string
	if msg == nil || msg.Body.DonId == "" {
		serviceName := jsonRequest.ServiceName()
		if handler, ok := g.serviceToMultiHandler[serviceName]; ok {
			h = handler
			handlerKey = serviceName
		} else if donID, ok := g.serviceNameToDonID[serviceName]; ok {
			// Fallback to legacy service name -> DON ID mapping
			if handler, ok := g.handlers[donID]; ok {
				h = handler
				handlerKey = donID
			}
		}
		if h == nil {
			return newError(jsonRequest.ID, api.HandlerError, "Service name not found: "+serviceName)
		}
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
	} else {
		method = jsonRequest.Method
		err = h.HandleJSONRPCUserMessage(ctx, jsonRequest, callback)
	}
	if err != nil {
		return newError(jsonRequest.ID, api.HandlerError, err.Error())
	}

	response, err := callback.Wait(ctx)
	duration := time.Since(startTime)
	if err != nil {
		response := api.RequestTimeoutError
		g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.String(), duration)
		g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.String())
		return newError(jsonRequest.ID, response, "handler timeout: "+err.Error())
	}
	g.gMetrics.RecordUserMsgHandlerDuration(ctx, method, response.ErrorCode.String(), duration)
	g.gMetrics.RecordUserMsgHandlerInvocation(ctx, method, response.ErrorCode.String())

	g.lggr.Debugw("received response from handler", "handler", handlerKey, "response", response, "requestID", jsonRequest.ID)
	promRequest.WithLabelValues(response.ErrorCode.String()).Inc()
	return response.RawResponse, api.ToHttpErrorCode(response.ErrorCode)
}
```

**File:** core/services/gateway/handlers/functions/handler.functions.go (L208-248)
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
