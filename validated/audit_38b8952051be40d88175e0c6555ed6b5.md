### Title
Unauthenticated resource-exhaustion / DON fan-out amplification via missing rate limiting in `handler.HandleJSONRPCUserMessage` - ([File: core/services/gateway/handlers/confidentialrelay/handler.go])

### Summary
`handler.HandleJSONRPCUserMessage` only validates that `req.ID` is non-empty and ≤200 characters before creating an `activeRequest` entry and fanning the request out to every DON member. Unlike the sibling vault handler (which is configured with `NodeRateLimiter` / per-sender & global RPS limits applied to inbound user requests), the confidential relay handler has no request-admission rate limiting or per-sender throttling on this path — the only rate limiters that exist (`globalNodeRateLimiter`, `perNodeRateLimiters`) are applied solely to `HandleNodeMessage` (node → gateway responses), not to user-submitted requests.

### Finding Description
`core/services/gateway/gateway.go`'s `ProcessRequest` exposes an unauthenticated HTTP endpoint that decodes any JSON-RPC request and, based on the service name / method, routes it to the matching handler's `HandleJSONRPCUserMessage` [1](#0-0) . No signature, allowlist, or authorization check gates this call for the confidential relay service/method — that logic exists only downstream inside the enclave/node handler (`core/capabilities/confidentialrelay/handler.go`, `verifyWorkflowAuthorization`), which runs after the gateway has already fanned the raw request to every DON member [2](#0-1) .

Inside `HandleJSONRPCUserMessage`, the only admission checks are on `req.ID` length/emptiness [3](#0-2) . Every request that passes this trivial check:
1. Allocates a new `activeRequest` map entry keyed by the attacker-supplied `req.ID`, held in `h.activeRequests` until `requestTimeout` (default 30s) elapses [4](#0-3) .
2. Immediately fans the request out over the persistent connections to *every* DON member via `errgroup` [5](#0-4) .

There is no cap on the number of concurrent `activeRequests`, no per-sender or global RPS limiter, and no admission control comparable to the vault handler's `NodeRateLimiter` config (`GlobalRPS`, `GlobalBurst`, `PerSenderRPS`, `PerSenderBurst`) seen wired into vault's handler tests [6](#0-5) . The confidential relay `Config` struct contains only timeout/grace fields and no rate-limiter configuration at all [7](#0-6) .

Because the gateway "makes no trust decision" and defers all authorization to the enclave (as documented in `forwardBundle`'s comment) [8](#0-7) , an unprivileged, unauthenticated caller of the gateway's user-facing HTTP endpoint can generate unlimited unique-ID requests, each of which:
- Grows the in-memory `activeRequests` map unboundedly until the periodic cleanup goroutine (`removeExpiredRequests`, run once per second) catches up, and
- Triggers `N` outbound sends to DON members per request (amplification factor = number of DON members), consuming node websocket bandwidth/CPU and gateway goroutines.

### Impact Explanation
This is a Denial-of-Service / resource-exhaustion vector reachable by any unauthenticated, unprivileged client with network access to the gateway's user HTTP endpoint. It can exhaust gateway memory (unbounded `activeRequests` map growth), exhaust send-side goroutines/errgroups, and flood every member of the DON with fan-out traffic, degrading gateway/DON availability for legitimate confidential-relay users. This matches a "Denial of Service (temporary node/network degradation)" bounty impact category rather than a critical fund-loss/compromise, since no secrets, keys, or unauthorized transactions can be obtained through this path alone (the enclave-side `verifyWorkflowAuthorization` still blocks unauthorized secret/capability access).

### Likelihood Explanation
High feasibility and full repeatability: the attacker needs no credentials, no valid signature, and no privileged access — only the ability to send HTTP requests to the gateway's user-facing endpoint with unique JSON-RPC IDs and the `secretsGet`/`capabilityExec` method names. The attack can be automated trivially and repeated indefinitely at the attacker's chosen rate, since there is no rate limiter that would return `HandlerError`/throttle before `fanOutToNodes` executes.

### Recommendation
Add request-admission throttling to `handler.HandleJSONRPCUserMessage` in `core/services/gateway/handlers/confidentialrelay/handler.go`, mirroring the vault handler's model:
- Add a `NodeRateLimiter`-style config (global + per-sender RPS/burst) to `Config` and enforce it before `newActiveRequest`/`fanOutToNodes` are invoked.
- Cap the maximum number of concurrent `activeRequests` (e.g., per DON, per caller/IP) and reject new requests with a clear error once the cap is reached.
- Consider requiring/verifying a lightweight sender identity or API auth token upstream of the fan-out, so unauthenticated peers cannot trigger unlimited DON-wide amplification.

### Proof of Concept
Unit test plan (add to `core/services/gateway/handlers/confidentialrelay/handler_test.go`):
1. Construct a `handler` via `NewHandler` with a `donConfig` containing e.g. 4 members, and a mock `gwhandlers.DON` whose `SendToNode` just counts invocations (`atomic.Uint64`).
2. In a tight loop, call `h.HandleJSONRPCUserMessage(ctx, req, callback)` 10,000 times with unique `req.ID` values (e.g., `uuid.NewString()`), all with `Method: MethodSecretsGet` and no signature/authorization data, and without waiting for/consuming responses.
3. Assert:
   - No error is ever returned by `HandleJSONRPCUserMessage` for any of the calls (no throttling occurs).
   - `SendToNode` was invoked at least `10000 * 4` times (fan-out amplification), demonstrating unauthenticated, unbounded outbound traffic to all DON members.
   - `len(h.activeRequests)` (inspected via a test-only accessor or by reflection) grows linearly with the number of calls before the periodic cleanup runs, demonstrating unbounded memory growth.
4. Contrast with an equivalent test against `vault/handler.go`, where a configured `NodeRateLimiter` causes some calls to be rejected/throttled, showing the confidential relay handler lacks the analogous protection.

### Citations

**File:** core/services/gateway/gateway.go (L264-273)
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
```

**File:** core/capabilities/confidentialrelay/handler.go (L653-667)
```go
// verifyWorkflowAuthorization is the PRIV-433 check beyond attestation. Attestation only
// proves the request came from genuine enclave code; it does not prove the Workflow DON
// authorized fetching this owner's secrets. A compromised TEE would still pass attestation
// while self-asserting a victim's owner.
//
// The enclave forwards the Workflow-DON-signed compute requests it executed (a 2*F+1 quorum,
// where F is the Workflow DON fault tolerance). Each node signs the same ComputeRequest.Hash();
// we reconstruct that hash, verify each signature against the onchain Workflow DON signer set,
// and require the quorum of unique signers. The signed PublicData names the authorized owner
// and workflow, which must match this request. A breached enclave cannot forge a Workflow DON
// quorum over a different owner.
//
// All failures here are client errors: the request is unauthorized. The caller fetches the
// Workflow DON (a server-side concern) and passes it in, so registry failures stay internal.
func (h *Handler) verifyWorkflowAuthorization(don capabilities.DON, params confidentialrelaytypes.SecretsRequestParams) error {
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L156-172)
```go
type Config struct {
	RequestTimeoutSec int `json:"requestTimeoutSec"`

	// NodeSendTimeoutSec bounds each individual fan-out send to a single relay node, and is
	// clamped to RequestTimeoutSec. It must stay below it so that one node whose connection
	// accepts no writes cannot delay delivery to the rest of the DON.
	NodeSendTimeoutSec int `json:"nodeSendTimeoutSec"`

	// QuorumGraceMillis is how long the handler keeps collecting responses after the
	// first F+1 signed responses arrive, before forwarding whatever it has. It bounds
	// the wait for a DON that answers with quorum but never reaches 2F+1 signed
	// responses, which would otherwise hold the request until RequestTimeoutSec and
	// forward a long-viable bundle after the caller's own deadline has passed.
	// Clamped to RequestTimeoutSec; a negative value disables the grace window and
	// restores waiting until expiry.
	QuorumGraceMillis int `json:"quorumGraceMillis"`
}
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L349-355)
```go
func (h *handler) HandleJSONRPCUserMessage(ctx context.Context, req jsonrpc.Request[json.RawMessage], callback gwhandlers.Callback) error {
	if req.ID == "" {
		return errors.New("request ID cannot be empty")
	}
	if len(req.ID) > 200 {
		return errors.New("request ID is too long: " + strconv.Itoa(len(req.ID)) + ". max is 200 characters")
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

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L593-595)
```go
// forwardBundle sends a previously-built bundle to the enclave. The gateway makes
// no trust decision; the enclave verifies signatures and reaches quorum.
func (h *handler) forwardBundle(ctx context.Context, l logger.Logger, ar *activeRequest, summary *BundleSummary) error {
```

**File:** core/services/gateway/handlers/confidentialrelay/handler.go (L618-641)
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

```

**File:** core/services/gateway/handlers/vault/handler_test.go (L260-268)
```go
		handlerConfig := Config{
			RequestTimeoutSec: 30,
			NodeRateLimiter: ratelimit.RateLimiterConfig{
				GlobalRPS:      100,
				GlobalBurst:    100,
				PerSenderRPS:   10,
				PerSenderBurst: 10,
			},
		}
```
