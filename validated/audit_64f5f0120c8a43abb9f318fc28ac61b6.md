### Title
Unbounded memory growth in `RequestReplayGuard.seen` via authorized-but-attacker-controllable distinct digests - ([File: core/capabilities/vault/request_replay_guard.go])

### Summary
`RequestReplayGuard` stores every authorized request digest in an in-memory map keyed by digest with no maximum size, and only reclaims memory for entries whose expiry has already elapsed. An authenticated (but otherwise unprivileged, non-admin) Vault caller who can obtain many distinct authorized digest/expiry pairs — either by allowlisting many distinct on-chain requests with long `ExpiryTimestamp` for a workflow they control, or by presenting many valid JWTs each bound to a distinct `request_digest` with far-future `exp` — can grow `seen` without bound until entries naturally expire, exhausting node memory.

### Finding Description
`CheckAndRecord` unconditionally inserts `digest -> expiresAtUnix` into `g.seen` after calling `clearExpiredLocked()`, which only deletes entries whose expiry timestamp is already in the past: [1](#0-0) [2](#0-1) 

There is no cap on `len(g.seen)`, no LRU eviction, and no bound tied to the number of distinct callers or requests. Both authorization paths feed into this single shared guard via `authorizer.AuthorizeRequest`: [3](#0-2) 

- AllowListBasedAuth: any caller who controls (or has allowlisted) a workflow can register many distinct `WorkflowRegistryOwnerAllowlistedRequest` entries with `ExpiryTimestamp` up to `uint32` max (~year 2106) via the on-chain WorkflowRegistry, then issue the corresponding requests. Each distinct request/params/id combination produces a unique digest that is authorized and stored: [4](#0-3) 

- JWTBasedAuth: a caller holding a valid signed JWT for their own org/tenant sets `expiresAt` from the token's `exp` claim plus leeway, with no maximum-lifetime enforcement in this path (unlike `core/utils/jwt.go`'s `VerifyRequestJWT`, which enforces `maxJWTExpiryDuration`): [5](#0-4) [6](#0-5) 

In both cases, once a request is legitimately authorized (not spoofed — signature/digest/owner checks are correctly enforced elsewhere), the replay guard records it indefinitely until its self-declared expiry. Neither path enforces a maximum expiry window nor a cap on the number of distinct digests a single caller may register, so a caller who is not a node operator/admin can generate an effectively unbounded number of long-lived map entries.

### Impact Explanation
Unbounded growth of `seen` consumes heap memory proportional to the number of distinct authorized (digest, expiry) pairs an attacker can generate before their earliest expiry elapses. Since the guard is process-wide and shared across all Vault callers (`authorizer.replayGuard` is a single instance created in `NewAuthorizer`), sustained abuse by one caller degrades or exhausts memory for the entire node process, denying the Vault capability to all users and potentially causing OOM-driven node crash — a node-wide availability impact.

### Likelihood Explanation
This requires the attacker to be able to produce many distinct authorized digests with long expiries repeatedly:
- Via AllowListBasedAuth this requires on-chain transactions to add allowlist entries for a workflow the attacker owns (gas cost provides a natural but not code-enforced rate limit).
- Via JWTBasedAuth this requires valid signed tokens from the trusted issuer bound to distinct digests/exp, which the caller can request as many times as their client-side workflow allows, with no per-caller quota enforced in `jwt_based_auth.go`.

Neither path requires node-operator/admin privilege, leaked keys, or social engineering — only ordinary, already-authenticated Vault client capabilities — making this reachable by an unprivileged (but authenticated) caller, repeatable over time, and bounded only by economic/API-call cost rather than by any code-level safeguard.

### Recommendation
Add a bound to `RequestReplayGuard`: enforce a maximum map size (evicting oldest/soonest-expiring entries or rejecting new authorizations once the cap is hit), and/or enforce a maximum allowed `expiresAtUnix - now` (analogous to `maxJWTExpiryDuration` used in `core/utils/jwt.go`) in both `allowListBasedAuth.AuthorizeRequest` and `jwtBasedAuth.AuthorizeRequest` so no single authorized entry can persist for an unbounded time. Consider also rate-limiting/capping distinct authorizations per owner/org within a time window.

### Proof of Concept
Unit test plan for `core/capabilities/vault/request_replay_guard_test.go`:
1. Construct `NewRequestReplayGuard()`.
2. In a loop of N (e.g., 1,000,000) iterations, call `CheckAndRecord(fmt.Sprintf("digest-%d", i), farFutureExpiry)` where `farFutureExpiry` is `time.Now().Unix() + 10*365*24*3600` (10 years out, analogous to a long-lived allowlist `ExpiryTimestamp` or far-future JWT `exp`).
3. Assert every call returns `nil` (no rejection) and that `len(guard.seen)` grows linearly to N with no eviction, demonstrating unbounded growth.
4. Add an invariant test asserting `len(guard.seen) <= someConfiguredCap` after inserting more than the cap — this should currently fail, proving there is no bound enforced by the guard.

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

**File:** core/capabilities/vault/request_replay_guard.go (L57-64)
```go
func (g *RequestReplayGuard) clearExpiredLocked() {
	now := g.nowFunc().UTC().Unix()
	for digest, expiry := range g.seen {
		if now > expiry {
			delete(g.seen, digest)
		}
	}
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

**File:** core/capabilities/vault/allow_list_based_auth.go (L34-76)
```go
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

	if time.Now().UTC().Unix() > int64(allowlistedRequest.ExpiryTimestamp) {
		authorizedRequestStr := string(allowlistedRequest.RequestDigest[:])
		r.lggr.Debugw("AllowListBasedAuth authorization expired", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", authorizedRequestStr, "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
		return nil, errors.New("request authorization expired")
	}

	digestKey := string(allowlistedRequest.RequestDigest[:])
	r.lggr.Debugw("AllowListBasedAuth authorization succeeded", "method", req.Method, "requestID", req.ID, "authorizedRequestStr", digestKey, "owner", allowlistedRequest.Owner.Hex(), "expiryTimestamp", allowlistedRequest.ExpiryTimestamp)
	return &AuthResult{
		workflowOwner: allowlistedRequest.Owner.Hex(),
		digest:        digestKey,
		expiresAt:     int64(allowlistedRequest.ExpiryTimestamp),
	}, nil
```

**File:** core/capabilities/vault/jwt_based_auth.go (L252-277)
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
}
```

**File:** core/capabilities/vault/jwt_based_auth.go (L302-313)
```go
	token, err := jwt.Parse(tokenString, func(token *jwt.Token) (any, error) {
		if _, methodOK := token.Method.(*jwt.SigningMethodRSA); !methodOK {
			return nil, fmt.Errorf("%w: unsupported alg %v", ErrInvalidToken, token.Header["alg"])
		}
		return rsaKey, nil
	},
		jwt.WithIssuer(v.issuerURL),
		jwt.WithAudience(v.audience),
		jwt.WithExpirationRequired(),
		jwt.WithIssuedAt(),
		jwt.WithLeeway(jwtValidationLeeway),
	)
```
