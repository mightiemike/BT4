### Title
Vault gateway `RequestReplayGuard` allows an unprivileged attacker to grief legitimate secrets requests by front-running the replay check with an identical request digest - ([File: core/capabilities/vault/authorizer.go])

### Summary
The Vault Gateway authorization pipeline uses a shared, in-memory `RequestReplayGuard` keyed by the deterministic request digest to prevent replay of Vault JSON-RPC requests. Because the digest is computed purely from the request wire body (method/params/id), and the guard is a global "first writer wins" lock, any unprivileged client that can obtain or predict a valid caller's exact pending request body can submit it first through the same public Gateway endpoint. The victim's legitimate (and equally authorized) request is then permanently rejected with `ErrRequestAlreadySeen` until that specific authorization (allowlist entry / JWT) expires, mirroring the `nonETHReuse`-style griefing bug: a shared "already used" flag that any caller can flip to lock out another caller's otherwise-valid call, with no way to recover except re-doing the privileged setup step (re-allowlisting on-chain or minting a new JWT with a new digest).

### Finding Description
`RequestReplayGuard.CheckAndRecord` is a simple map keyed by `digest`; the first caller for a given digest succeeds, and any subsequent caller with the same digest is rejected: [1](#0-0) 

This guard is invoked once per request, after either `AllowListBasedAuth` or `JWTBasedAuth` produces an `AuthResult`, and *before* the request is dispatched to the underlying secrets service: [2](#0-1) 

The digest used as the guard key is `req.Digest()`, a deterministic hash of the request's wire bytes (method, id, params) — not a per-submission nonce controlled by the node or tied to a specific submitter/IP. For `AllowListBasedAuth`, this digest is exactly the value that gets allowlisted on-chain in the `WorkflowRegistry` (`AllowlistRequest(requestDigest, expiry)`), which is publicly observable on-chain: [3](#0-2) [4](#0-3) 

For `JWTBasedAuth`, the digest is embedded in the token's `authorization_details` claim and is verified to match the presented request body: [5](#0-4) 

Both authorization paths are reachable by any client that can reach the public HTTP Gateway (an unprivileged, unauthenticated network trust boundary), which forwards the JSON-RPC request through `GatewayVaultRequestProcessor.ProcessRequest` → `authorizeAndStamp` → `Authorizer.AuthorizeRequest`: [6](#0-5) [7](#0-6) 

Because the wire body (and therefore the digest) is exactly what gets authorized — whether via the on-chain allowlist entry (public) or a JWT (which the legitimate caller must transmit to the Gateway in plaintext HTTP over the wire) — an attacker who can observe or reconstruct a still-valid, not-yet-processed request's exact bytes (e.g., via network interception of the plaintext HTTP call to the Gateway, or because the underlying secret parameters/request_id are otherwise learnable) can resubmit the identical JSON-RPC payload to the same Gateway endpoint before the legitimate request lands. Whichever copy the authorizer processes first "wins" the digest slot in the replay guard; the other is rejected with `ErrRequestAlreadySeen`. This exactly parallels the `nonETHReuse` bug class: a single shared boolean/state flag ("digest seen") that any unprivileged party can set first to lock out a legitimate, independently-authorized caller, and the only "unlock" mechanism is to redo the privileged setup (re-allowlist with a new digest on-chain, or mint a fresh JWT bound to a new digest) rather than simply retrying the call.

### Impact Explanation
Impact is Medium/DoS: a legitimate, correctly-authorized Vault operation (secrets create/update/delete/list) for a workflow owner can be made to fail non-deterministically, and the failure persists until the specific authorization (on-chain allowlist entry or JWT) expires — the request cannot simply be retried with the same body, since the digest is now permanently marked "seen" for the life of that authorization window. This can disrupt secret provisioning/rotation of a workflow, which is part of the node's privileged secret-management trust boundary. It does not itself achieve secret disclosure or unauthorized state mutation, but it is an availability/tampering issue for a security-relevant control (Vault secrets operations gated by cryptographic authorization).

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to learn the exact bytes of a still-pending, not-yet-consumed authorized request before it is processed by a node's Gateway handler. This is plausible when: (a) the AllowListBasedAuth digest is emitted on-chain in the `AllowlistRequest` event/logs before the corresponding off-chain JSON-RPC call is sent to the Gateway, giving an attacker a timing window to guess/reconstruct and race the exact request bytes to the Gateway; or (b) network-level observation of the plaintext HTTP request to the Gateway is possible. It does not require any node/operator collusion — a pure unprivileged external actor interacting with the public Gateway HTTP endpoint suffices, satisfying the "unprivileged-user analog" requirement.

### Recommendation
- Scope the replay guard per authorized submitter/session rather than purely by request-content digest, or combine the digest with a caller-bound, single-use nonce that is not independently reproducible/observable by third parties.
- For AllowListBasedAuth, avoid using the same digest that is publicly emitted on-chain as the sole replay-guard key; instead derive the guard key including data not visible before the legitimate request reaches the Gateway (e.g., a server-issued nonce, or bind to the caller's session/connector identity).
- Return a distinguishable error/backoff (rather than a hard, non-retryable "already seen" for the authorization's entire remaining lifetime) so a legitimate caller whose request was raced can detect the collision and re-issue a fresh authorization promptly rather than waiting for the original digest expiry.
- Rate-limit/anomaly-detect duplicate digests arriving from different Gateway connections in a short window, which is a strong signal of this race.

### Proof of Concept
1. Workflow owner allowlists a Vault request on-chain via `WorkflowRegistry.AllowlistRequest(requestDigest, expiry)` (as in `allowlistRequest` helper), which publicly emits `requestDigest`. [4](#0-3) 
2. Attacker observes/reconstructs the exact JSON-RPC wire body corresponding to that digest (e.g., by monitoring the plaintext HTTP call the legitimate client is about to send to the public Gateway endpoint, since the digest itself does not need to be reversed — only the same bytes need to be resent).
3. Attacker submits that identical JSON-RPC request to the Gateway first. `AllowListBasedAuth.AuthorizeRequest` succeeds (digest is allowlisted, not expired), and `authorizer.AuthorizeRequest` calls `replayGuard.CheckAndRecord(digest, expiresAt)`, which succeeds and marks the digest "seen": [8](#0-7) 
4. The legitimate owner's original request, carrying the same digest, now hits `CheckAndRecord` and is rejected with `ErrRequestAlreadySeen`: [9](#0-8) 
5. The owner's request fails and cannot be retried with the same allowlisted digest; a new on-chain `AllowlistRequest` call (new digest) is required to make progress — directly analogous to needing a `Multicall` "unlock" step in the original report.

### Citations

**File:** core/capabilities/vault/request_replay_guard.go (L35-47)
```go
func (g *RequestReplayGuard) CheckAndRecord(digest string, expiresAtUnix int64) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	g.clearExpiredLocked()

	if _, exists := g.seen[digest]; exists {
		return ErrRequestAlreadySeen
	}

	g.seen[digest] = expiresAtUnix
	return nil
}
```

**File:** core/capabilities/vault/authorizer.go (L99-119)
```go
func (a *authorizer) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	authResult, err := a.authorizeRequest(ctx, req)
	if err != nil {
		return nil, err
	}
	if authResult == nil {
		err = errors.New("auth mechanism returned nil auth result")
		a.lggr.Errorw("auth mechanism returned nil auth result", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "")
		return nil, err
	}
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
	if ownerErr := validateSecretOwnersMatchAuthorized(req, authResult.AuthorizedOwner()); ownerErr != nil {
		a.lggr.Errorw("owner binding rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "hasAuth", req.Auth != "", "error", ownerErr)
		return nil, ownerErr
	}
	a.lggr.Debugw("request authorized", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "")
	return authResult, nil
}
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L32-62)
```go
// AuthorizeRequest authorizes a request using AllowListBasedAuth.
// It does NOT check if the request method is allowed.
func (r *allowListBasedAuth) AuthorizeRequest(ctx context.Context, req jsonrpc.Request[json.RawMessage]) (*AuthResult, error) {
	r.lggr.Debugw("AllowListBasedAuth authorizing request", "method", req.Method, "requestID", req.ID)
	requestDigest, err := req.Digest()
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to create digest", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, err
	}
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	if err != nil {
		r.lggr.Debugw("AllowListBasedAuth failed to decode digest", "method", req.Method, "requestID", req.ID, "requestDigest", requestDigest, "error", err)
		return nil, err
	}
	requestDigestBytes32 := [32]byte(requestDigestBytes)
	if r.workflowRegistrySyncer == nil {
		r.lggr.Errorw("AllowListBasedAuth workflowRegistrySyncer is nil", "method", req.Method, "requestID", req.ID)
		return nil, errors.New("internal error: workflowRegistrySyncer is nil")
	}
	allowlistedRequest, allowedRequestsStrs, err := r.findAllowlistedItemWithRetry(ctx, req, requestDigest, requestDigestBytes32)
	if err != nil {
		return nil, err
	}
	if allowlistedRequest == nil {
		r.lggr.Debugw("AllowListBasedAuth request digest not allowlisted",
			"method", req.Method,
			"requestID", req.ID,
			"digestHexStr", requestDigest,
			"allowedRequestsStrs", allowedRequestsStrs)
		return nil, errors.New("request not allowlisted")
	}
```

**File:** system-tests/tests/smoke/cre/vault_don_test_helpers.go (L1376-1393)
```go
func allowlistRequest(t *testing.T, owner string, request jsonrpc.Request[json.RawMessage], sethClient *seth.Client, wfRegistryContract *workflow_registry_v2_wrapper.WorkflowRegistry) {
	requestDigest, err := request.Digest()
	require.NoError(t, err, "failed to get digest for request")
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	require.NoError(t, err, "failed to decode digest")
	reqDigestBytes := [32]byte(requestDigestBytes)
	_, err = wfRegistryContract.AllowlistRequest(sethClient.NewTXOpts(), reqDigestBytes, uint32(time.Now().Add(1*time.Hour).Unix())) //nolint:gosec // disable G115
	require.NoError(t, err, "failed to allowlist request")

	framework.L.Info().Msgf("Allowlisting request digest at contract %s, for owner: %s, digestHexStr: %s", wfRegistryContract.Address().Hex(), owner, requestDigest)
	allowedList, err := wfRegistryContract.GetAllowlistedRequests(&bind.CallOpts{}, big.NewInt(0), big.NewInt(100))
	require.NoError(t, err, "failed to validate allowlisted request")
	for _, req := range allowedList {
		if req.RequestDigest == reqDigestBytes {
			framework.L.Info().Msgf("Request digest found in allowlist")
		}
		framework.L.Info().Msgf("Allowlisted request digestHexStr: %s, owner: %s, expiry: %d", hex.EncodeToString(req.RequestDigest[:]), req.Owner.Hex(), req.ExpiryTimestamp)
	}
```

**File:** core/capabilities/vault/jwt_based_auth.go (L252-261)
```go
	requestDigest, err := req.Digest()
	if err != nil {
		v.lggr.Debugw("JWTBasedAuth failed to compute request digest", "method", req.Method, "requestID", req.ID, "orgID", claims.OrgID, "workflowOwner", claims.WorkflowOwner, "error", err)
		return nil, fmt.Errorf("failed to compute request digest: %w", err)
	}

	if !strings.EqualFold(requestDigest, claims.RequestDigest) {
		v.lggr.Debugw("JWTBasedAuth request digest mismatch", "method", req.Method, "requestID", req.ID, "orgID", claims.OrgID, "workflowOwner", claims.WorkflowOwner, "computedDigest", requestDigest, "claimedDigest", claims.RequestDigest)
		return nil, fmt.Errorf("request digest mismatch: computed=%s claimed=%s", requestDigest, claims.RequestDigest)
	}
```

**File:** core/capabilities/vault/gateway_vault_request_processor.go (L222-255)
```go
func (p *GatewayVaultRequestProcessor) authorizeAndStamp(
	ctx context.Context,
	req *jsonrpc.Request[json.RawMessage],
	stamp func(prefixedRequestID string) error,
) (*AuthorizedGatewayVaultRequest, error) {
	incomingOwner := ""
	if idx := strings.Index(req.ID, vaulttypes.RequestIDSeparator); idx != -1 {
		incomingOwner = req.ID[:idx]
	}

	p.lggr.Debugw("authorizing gateway vault request", "method", req.Method, "requestID", req.ID)
	authResult, err := p.authorizer.AuthorizeRequest(ctx, *req)
	if err != nil {
		authErr := fmt.Errorf("request not authorized: %w", err)
		p.lggr.Errorw("gateway vault request authorization failed", "method", req.Method, "requestID", req.ID, "hasAuth", req.Auth != "", "incomingOwner", incomingOwner, "error", authErr)
		return nil, authErr
	}

	originalRequestID := req.ID
	authorizedOwner := authResult.AuthorizedOwner()
	prefixedRequestID := authorizedOwner + vaulttypes.RequestIDSeparator + originalRequestID
	req.ID = prefixedRequestID

	if err := stamp(prefixedRequestID); err != nil {
		p.lggr.Errorw("failed to stamp authorized request params", "method", req.Method, "requestID", req.ID, "error", err)
		return nil, fmt.Errorf("failed to stamp authorized request params: %w", err)
	}

	p.lggr.Debugw("authorized gateway vault request", "method", req.Method, "requestID", req.ID, "owner", authorizedOwner, "orgID", authResult.OrgID(), "workflowOwner", authResult.WorkflowOwner())
	return &AuthorizedGatewayVaultRequest{
		Req:        *req,
		AuthResult: authResult,
	}, nil
}
```

**File:** core/capabilities/vault/gw_handler.go (L180-211)
```go
func (h *GatewayHandler) HandleGatewayMessage(ctx context.Context, gatewayID string, req *jsonrpc.Request[json.RawMessage]) (err error) {
	reqLggr := h.requestLogger(req, gatewayID)
	reqLggr.Debugw("received message from gateway", "req", req)

	var response *jsonrpc.Response[json.RawMessage]
	var authResult *AuthResult

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
	case vaulttypes.MethodSecretsDelete, vaulttypes.MethodSecretsList:
		authorized, pipelineErr := h.requestProcessor.ProcessRequest(ctx, req, nil)
		if pipelineErr != nil {
			response = h.gatewayErrorResponse(ctx, gatewayID, req, pipelineErr)
			break
		}
		authResult = authorized.AuthResult
	case vaulttypes.MethodPublicKeyGet:
		response = h.handlePublicKeyGet(ctx, gatewayID, req)
	default:
		response = h.errorResponse(ctx, gatewayID, req, api.UnsupportedMethodError, errors.New("unsupported method: "+req.Method))
	}
```
