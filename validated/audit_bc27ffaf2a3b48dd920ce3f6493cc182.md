### Title
Unbounded `activeRequests` map growth via unauthenticated MethodSecretsGet/MethodCapabilityExec requests - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` accepts any JSON-RPC user request reaching the gateway HTTP path and unconditionally inserts a new entry into `h.activeRequests` via `newActiveRequest`, with the only constraint being uniqueness of `req.ID` and a 200-character ID length cap. There is no per-caller or global admission control (rate limiter or concurrency cap) on this ingress path before state is created and fanned out to all DON members.

### Finding Description
The request path is: `network.httpServer.handleRequest` → `gateway.ProcessRequest` (only decodes/validates the JSON-RPC envelope and DON routing, no auth/rate limiting) → `handler.HandleJSONRPCUserMessage` → `h.newActiveRequest` [1](#0-0) . `newActiveRequest` only checks for an ID collision and otherwise inserts unconditionally, with no cap on `len(h.activeRequests)`: [2](#0-1) . The handler's `globalNodeRateLimiter`/`perNodeRateLimiters` are only consulted in `HandleNodeMessage`, which gates node→gateway traffic, not user-ingress traffic: [3](#0-2) . Each accepted request also triggers a fan-out send to every DON member (`fanOutToNodes`), so the attack additionally generates N outbound sends per forged request: [4](#0-3) . Expired entries are only reaped once per `defaultCleanUpPeriod` (1s) by `removeExpiredRequests`, and only after `h.requestTimeout` (default 30s) has elapsed [5](#0-4) [6](#0-5) , so an attacker can keep the map populated indefinitely by submitting new unique IDs faster than the 30s timeout window drains old ones. Comparable sibling handlers in the codebase (e.g., the v2 HTTP trigger handler) explicitly apply a `userRateLimiter`/`checkRateLimit` before admitting state [7](#0-6) , confirming this is a missing control relative to the codebase's own established pattern, not an intentional design choice.

### Impact Explanation
An unprivileged network client can flood the gateway with unique-ID `MethodSecretsGet`/`MethodCapabilityExec` requests without ever completing them (never causing node responses to arrive), keeping each entry alive for up to `requestTimeout` (default 30s) and growing `h.activeRequests` linearly with request rate. This causes unbounded memory growth on the gateway process, increases lock contention on `h.mu` (used by nearly every hot path: `newActiveRequest`, `getActiveRequest`, `removeExpiredRequests`, `forwardGracedRequests`, `sendResponseAndClearRequest`), and multiplies outbound fan-out load to every DON member, degrading or denying confidential relay service to legitimate users of the same DON.

### Likelihood Explanation
Feasibility is high: the only preconditions are network access to the gateway's HTTP endpoint and the ability to construct a syntactically valid JSON-RPC request (`req.ID` non-empty, ≤200 chars, targeting a DON that has this handler registered) — no authentication, signature, or node/operator privilege is required. The request size limiter (`MaxRequestBytesLimiter`) bounds the size of each request but not the rate or count of requests submitted, and the handler itself performs no per-sender or global rate limiting before allocating state.

### Recommendation
Add admission control on the user-ingress path in `confidentialrelay.handler` before `newActiveRequest` is called: enforce a global concurrency cap on `len(h.activeRequests)` (rejecting/backpressuring new requests once a configured maximum is reached) and/or a global/per-sender rate limiter analogous to `globalNodeRateLimiter`, mirroring the `userRateLimiter` pattern already used in `capabilities/v2/http_trigger_handler.go`. Also consider shrinking `defaultCleanUpPeriod`/`requestTimeout` bounds or adding a lighter-weight sweep specifically for never-responded entries.

### Proof of Concept
Add a unit/load test in `core/services/gateway/handlers/confidentialrelay/handler_test.go`:
1. Construct a `handler` with a mock `DON` whose `SendToNode` is a no-op that never triggers `HandleNodeMessage`.
2. Loop issuing N (e.g. 100k) `HandleJSONRPCUserMessage` calls with distinct `req.ID` values and `MethodSecretsGet`/`MethodCapabilityExec` methods, without ever calling `HandleNodeMessage`.
3. Assert that `len(h.activeRequests)` grows unboundedly to N (no rejection, no cap enforced) within `h.requestTimeout`, demonstrating the map is not bounded by any concurrency/rate limit — expected (vulnerable) behavior: all N entries accepted; expected (fixed) behavior: requests beyond a configured threshold are rejected with a rate-limit/capacity error before being added to `activeRequests`.

### Citations

**File:** core/services/gateway/gateway.go (L264-277)
```go
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

```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L30-37)
```go
const (
	// defaultCleanUpPeriod is how often expired requests are swept and closed grace
	// windows are forwarded, so it also bounds how far past its deadline a grace
	// window can run.
	defaultCleanUpPeriod = time.Second

	defaultRequestTimeoutSec  = 30
	defaultNodeSendTimeoutSec = 10
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L306-339)
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
		l := logger.With(h.lggr, "method", er.req.Method, "requestID", er.req.ID)
		l.Debugw("request expired, evaluating collected relay responses",
			"collected", len(responses),
			"nodes", len(h.donConfig.Members),
			"unanswered", len(h.donConfig.Members)-len(responses),
		)
		summary, err := h.bundler.Bundle(er.req, responses, l)
		if err != nil {
			l.Errorw("failed to build relay response bundle", "error", err)
			if sendErr := h.sendResponseAndClearRequest(ctx, er, h.constructErrorResponse(er.req, api.FatalError, err)); sendErr != nil {
				l.Errorw("error returning bundle failure on expiry", "error", sendErr)
			}
			continue
		}
		// Expiry makes further responses unavailable to this request. The common
		// readiness path forwards a viable partial bundle or returns a timeout.
		if err := h.forwardBundleOrTerminateIfReady(ctx, l, er, summary, 0, true); err != nil {
			l.Errorw("error forwarding bundle on expiry", "error", err)
		}
	}
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L368-383)
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L391-406)
```go
func (h *handler) HandleNodeMessage(ctx context.Context, resp *jsonrpc.Response[json.RawMessage], nodeAddr string) error {
	l := logger.With(h.lggr, "method", resp.Method, "requestID", resp.ID, "nodeAddr", nodeAddr)
	l.Debugw("handling node response")

	nodeRateLimiter, ok := h.perNodeRateLimiters[nodeAddr]
	if !ok {
		return fmt.Errorf("received message from unexpected node %s", nodeAddr)
	}
	if !nodeRateLimiter.Allow(ctx) {
		l.Debugw("node is rate limited", "nodeAddr", nodeAddr)
		return nil
	}
	if !h.globalNodeRateLimiter.Allow(ctx) {
		l.Debug("global relay rate limit exceeded")
		return nil
	}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-652)
```go
func (h *handler) fanOutToNodes(ctx context.Context, l logger.Logger, ar *activeRequest) error {
	var (
		group      errgroup.Group
		nodeErrors atomic.Uint32
	)

	// Each send is bounded independently. A node whose websocket accepts no writes blocks
	// until its context is cancelled, and because the caller only reads the response callback
	// after this function returns, an unbounded send would hold the request open until the
	// client gives up, discarding a bundle that already reached quorum.
	sendCtx, cancel := context.WithTimeout(ctx, h.nodeSendTimeout)
	defer cancel()

	for _, node := range h.donConfig.Members {
		group.Go(func() error {
			err := h.don.SendToNode(sendCtx, node.Address, &ar.req)
			if err != nil {
				nodeErrors.Add(1)
				l.Errorw("error sending request to node", "node", node.Address, "error", err)
			}
			return nil
		})
	}

	_ = group.Wait()

	numNodeErrors := nodeErrors.Load()
	remainingPossibleResponses := len(h.donConfig.Members) - int(numNodeErrors)
	if remainingPossibleResponses < h.donConfig.F+1 && numNodeErrors > 0 {
		return h.sendResponseAndClearRequest(ctx, ar, h.constructErrorResponse(ar.req, api.FatalError, errors.New("failed to forward user request to nodes")))
	}

	l.Debugw("successfully forwarded request to relay nodes")
	return nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L369-394)
```go
func (h *httpTriggerHandler) checkRateLimit(ctx context.Context, workflowID, requestID string, callback handlers.Callback) error {
	workflowRef, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflow reference not found", callback)
		return errors.New("workflow reference not found")
	}

	// TODO orgID https://smartcontract-it.atlassian.net/browse/CRE-1707
	ctx = contexts.WithCRE(ctx, contexts.CRE{Owner: workflowRef.workflowOwner, Workflow: workflowID})
	if err := h.userRateLimiter.AllowErr(ctx); err != nil {
		lggr := logger.With(h.lggr, platform.KeyWorkflowID, workflowID, platform.KeyWorkflowOwner, workflowRef.workflowOwner, "requestID", requestID, "err", err)
		var errLimited limits.ErrorRateLimited
		if errors.As(err, &errLimited) {
			switch errLimited.Scope {
			case settings.ScopeWorkflow:
				lggr.Errorf("failed to start execution: per workflow rate limit exceeded")
				h.metrics.IncrementWorkflowThrottled(ctx, h.lggr)
			default:
				lggr.Errorf("failed to start execution: unexpected rate limit for scope %s", errLimited.Scope)
			}
			h.handleUserError(ctx, requestID, jsonrpc.ErrLimitExceeded, "rate limit exceeded", callback)
			return err
		}
		return fmt.Errorf("failed to check rate limit: %w", err)
	}
	return nil
```
