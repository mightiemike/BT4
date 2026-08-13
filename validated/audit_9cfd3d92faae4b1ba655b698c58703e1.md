### Title
Replay guard digest keys are not canonical between allow-list and JWT auth paths, allowing single-use requests to be authorized twice - ([File: core/capabilities/vault/authorizer.go])

### Summary
`allowListBasedAuth.AuthorizeRequest` stores the replay-guard digest as the **raw 32 decoded bytes** of the request digest cast directly to a Go `string` (`string(allowlistedRequest.RequestDigest[:])`), while `jwtBasedAuth.AuthorizeRequest` stores the digest as the **hex-encoded string** returned directly by `req.Digest()`. For the identical underlying `jsonrpc.Request`, these two code paths therefore produce two structurally different string values that are never equal to each other, so `RequestReplayGuard.seen` records them under two distinct keys instead of one canonical key.

### Finding Description
In `core/capabilities/vault/allow_list_based_auth.go`: [1](#0-0) 
`req.Digest()` returns a hex string, which is `hex.DecodeString`'d into 32 raw bytes and matched against on-chain allowlist entries. The `AuthResult.digest` is then set to the **raw byte** representation, not the hex string: [2](#0-1) 

In `core/capabilities/vault/jwt_based_auth.go`, the digest stored in `AuthResult` is the hex string returned directly by `req.Digest()` (not even `claims.RequestDigest`, which is only used for a case-insensitive equality check via `strings.EqualFold`): [3](#0-2) 

Both digests are computed from the same `req.Digest()` call for the same request, but the allow-list path re-encodes it into raw bytes while the JWT path keeps the original hex string. `AuthResult.Digest()` returns whichever internal `digest` field was set, with no normalization: [4](#0-3) 

The single shared `RequestReplayGuard` keys purely on this string value: [5](#0-4) [6](#0-5) 

Because the two paths never emit byte-for-byte identical digest strings for the same logical request (one is a 32-byte raw string, the other a 64-character hex string), the guard's core invariant — "canonical, identical key regardless of which authorizer produced it" — is violated. If the same request is independently authorized once via the allow-list mechanism and once via a valid JWT (each carrying a matching `authorization_details.request_digest`), the replay guard records two separate entries and permits the underlying privileged Vault action (e.g., `MethodSecretsCreate`/`MethodSecretsUpdate`/`MethodSecretsDelete`) to execute twice.

### Impact Explanation
This breaks the single-use execution guarantee for privileged Vault RPC methods. A request meant to be authorized exactly once (per the `RequestReplayGuard` design comment) can instead execute twice — once through each auth mechanism — for the same underlying secret-management operation. This corresponds to unauthorized repeated execution of a privileged/state-changing action (secret create/update/delete), a data-integrity/replay-protection bypass rather than a pure logging inconsistency.

### Likelihood Explanation
Exploitation requires the same request to be independently authorizable through *both* mechanisms: a matching on-chain workflow-registry allowlist entry (`WorkflowRegistryOwnerAllowlistedRequest.RequestDigest`/`ExpiryTimestamp`) and a validly-signed Auth0 JWT whose `authorization_details.request_digest` matches the same digest and whose owner/tenant checks pass. Obtaining a valid signed JWT and a matching on-chain allowlist entry both require legitimate credentials/authorization that an unprivileged external attacker without leaked keys cannot forge; this scenario is realistically reachable only by an already-authorized workflow owner who is dual-provisioned under both mechanisms (e.g., during a migration window where both allow-list and JWT auth are active for the same tenant/workflow). It is a genuine correctness defect in the replay guard's canonicalization but its exploitation window depends on both authorization paths being independently satisfiable for the same request, which is not attacker-controlled without prior legitimate authorization.

### Recommendation
Canonicalize the digest before storing/comparing in `AuthResult`/`RequestReplayGuard`. Both `allow_list_based_auth.go` and `jwt_based_auth.go` should store the exact same encoding — e.g., always use the lowercase hex string returned by `req.Digest()` (as `jwtBasedAuth` almost does) instead of re-deriving raw bytes in `allowListBasedAuth`. Concretely, change `core/capabilities/vault/allow_list_based_auth.go` line 70 from `digestKey := string(allowlistedRequest.RequestDigest[:])` to `digestKey := requestDigest` (the hex string already validated against `requestDigestBytes32`), and ensure `jwtBasedAuth` also normalizes case (e.g., `strings.ToLower(requestDigest)`) before assigning to `AuthResult.digest`, so both authorizers always produce identical canonical strings for the same request.

### Proof of Concept
Differential unit test in `core/capabilities/vault`:
1. Construct a `jsonrpc.Request[json.RawMessage]` `req` and compute `wantDigest, _ := req.Digest()`.
2. Drive `allowListBasedAuth.AuthorizeRequest(ctx, req)` with a stub `workflowRegistrySyncer` returning a `WorkflowRegistryOwnerAllowlistedRequest{RequestDigest: decodedBytes32(wantDigest), ExpiryTimestamp: future}`; capture `resultA.Digest()`.
3. Drive `jwtBasedAuth.AuthorizeRequest(ctx, req)` with a JWKS/token stub whose claims include `authorization_details.request_digest = wantDigest` and matching org/tenant; capture `resultB.Digest()`.
4. Assert `resultA.Digest() != resultB.Digest()` today (proving the bug), and after the fix assert `resultA.Digest() == resultB.Digest()` byte-for-byte.
5. Integration-level PoC: call `authorizer.AuthorizeRequest` twice for the same `req` — first with `req.Auth == ""` (allow-list path) and then with `req.Auth` set to the matching JWT — and assert that the second call currently succeeds (no `ErrRequestAlreadySeen`), demonstrating the double-authorization; after the fix, assert the second call returns `ErrRequestAlreadySeen`.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L36-46)
```go
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
```

**File:** core/capabilities/vault/allow_list_based_auth.go (L70-76)
```go
	digestKey := string(allowlistedRequest.RequestDigest[:])
	r.lggr.Debugw("AllowListBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", digestKey, "owner", allowlistedRequest.Owner.Hex(), "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
	return &AuthResult{
		workflowOwner: allowlistedRequest.Owner.Hex(),
		digest:        digestKey,
		expiresAt:     int64(allowlistedRequest.ExpiryTimestamp),
	}, nil
```

**File:** core/capabilities/vault/jwt_based_auth.go (L252-276)
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

	derivedWorkflowOwner, err := DeriveJWTAuthorizedVaultWorkflowOwner(claims.OrgID, claims.TenantID, claims.WorkflowOwner)
	if err != nil {
		v.lggr.Debugw("JWTBasedAuth failed to derive authorized workflow owner", "method", req.Method, "requestID", req.ID, "orgID", claims.OrgID, "error", err)
		return nil, fmt.Errorf("invalid JWT auth token: %w", err)
	}

	authExpiresAt := claims.ExpiresAt.UTC().Add(jwtValidationLeeway).Unix()
	v.lggr.Debugw("JWTBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "orgID", claims.OrgID, "workflowOwner", derivedWorkflowOwner, "digest", requestDigest, "expiresAt", authExpiresAt)
	return &AuthResult{
		orgID:         claims.OrgID,
		workflowOwner: derivedWorkflowOwner,
		digest:        requestDigest,
		expiresAt:     authExpiresAt,
	}, nil
```

**File:** core/capabilities/vault/authorizer.go (L61-67)
```go
// Digest returns the request digest used for replay protection.
func (a *AuthResult) Digest() string {
	if a == nil {
		return ""
	}
	return a.digest
}
```

**File:** core/capabilities/vault/authorizer.go (L109-112)
```go
	if err := a.replayGuard.CheckAndRecord(authResult.Digest(), authResult.ExpiresAt()); err != nil {
		a.lggr.Debugw("replay guard rejected request", "method", req.Method, "requestID", req.ID, "owner", authResult.AuthorizedOwner(), "digest", authResult.Digest(), "expiresAt", authResult.ExpiresAt(), "hasAuth", req.Auth != "", "error", err)
		return nil, err
	}
```

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
