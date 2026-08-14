### Title
Vault AllowlistBasedAuth Requests Cannot Be Revoked Before Expiry, Allowing Delayed/Stale Execution of Pre-Authorized Vault Requests - (File: `core/capabilities/vault/allow_list_based_auth.go`)

### Summary
The Vault `AllowListBasedAuth` mechanism authorizes JSON-RPC requests (e.g. `vault.secrets.create`) purely based on whether the request's digest has been allowlisted on-chain via the `WorkflowRegistry` contract's `AllowlistRequest(digest, expiryTimestamp)` method, checked only against an `ExpiryTimestamp` [1](#0-0) . Like the RFQ order's `expires` field, this is the only invalidation mechanism — there is no on-chain method to revoke an allowlisted request digest before its expiry.

### Finding Description
`AuthorizeRequest` in `allow_list_based_auth.go` fetches allowlisted request digests synced from the `WorkflowRegistry` contract and only rejects a request if the current time exceeds `ExpiryTimestamp`; otherwise, the exact-match digest is authorized [2](#0-1) . The syncer (`workflow_registry.go`) periodically polls the contract and only prunes entries once `ExpiryTimestamp` has passed — active entries remain valid for the DON to authorize until they naturally expire [3](#0-2) .

Searching the codebase for a corresponding "remove"/"revoke" allowlist entry point (analogous to the RFQ report's recommended fix of letting the market maker revoke a specific order hash) turned up nothing: there is no `RemoveAllowlistedRequest`/`RevokeAllowlistedRequest` method anywhere in the contract bindings, the syncer, or the changeset tooling (`user_workflow_registry_ops.go`, `user_workflow_registry.go`) — only `AllowlistRequest` (add) and `GetAllowlistedRequests`/`TotalAllowlistedRequests` (read) exist. This mirrors the RFQ bug exactly: a workflow owner can pre-authorize a specific request digest for the DON (e.g., a request to write particular secrets, with a payload baked into the digest) with an expiry far in the future, but has no way to invalidate that authorization early if conditions change (e.g., the owner wants to retract a pending secrets-write once new information becomes available, or a compromised/erroneously-signed request needs to be pulled).

The `RequestReplayGuard` does prevent the *same* digest from being reused twice [4](#0-3) , which limits impact to single-use, but it does nothing for the window between allowlisting and the time the request is actually submitted — during that entire window (up to `ExpiryTimestamp`), the pre-authorized action remains executable exactly once, on the requester's/DON's timing rather than the owner's, with no cancellation path.

### Impact Explanation
An owner who allowlists a vault request with a long expiry cannot retract that authorization before expiry. If the underlying gateway/DON accepts and executes the request at any point in that window, the owner cannot prevent execution once conditions have changed (e.g., the params encoded in the digest become stale or undesirable), similarly to how in the RFQ bug a market maker cannot pull a signed price quote. This is a lower-severity match than the original financial-order case because Vault authorizations are single-use (via the replay guard) and are typically issued immediately before use, but it is a real trust-boundary gap: privileged pre-authorization state persists on-chain with no cancel/kill-switch capability, which is an unauthorized "stale execution" surface for privileged node/vault actions.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: exploiting this requires an owner to allowlist a request they later want to cancel, and for a third party (or automated flow) to submit that exact request before expiry. It's plausible in workflows where allowlisting happens well ahead of actual submission (e.g., pre-staged secrets writes for pipeline automation) rather than atomically at submission time.

### Recommendation
Add an on-chain revocation function to `WorkflowRegistry` (e.g., `revokeAllowlistedRequest(bytes32 requestDigest)`) restricted to the original `Owner`, and have the syncer (`workflow_registry.go`) react to a corresponding removal event by immediately purging the entry from `allowListedRequests`, rather than relying solely on `ExpiryTimestamp` pruning. This closes the "irrevocable authorization" window and matches the RFQ report's recommendation of allowing the counterparty to invalidate a specific commitment hash immediately rather than waiting for natural expiry.

### Proof of Concept
1. Workflow owner calls `WorkflowRegistry.AllowlistRequest(requestDigest, expiryTimestamp=now+24h)` to pre-authorize a `vault.secrets.create` request for the DON [5](#0-4) .
2. Owner later decides the request should not be executed (e.g., wrong secret content), but has no contract method to remove/revoke this entry — `AllowlistRequest` only adds, and no `revoke`/`remove` counterpart exists in the codebase.
3. At any point before `expiryTimestamp`, anyone submitting the exact same JSON-RPC request (same digest) to the Vault gateway passes `AuthorizeRequest` in `allow_list_based_auth.go:34-76`, since the only check performed is expiry, not owner intent/cancellation.
4. The request executes once (blocked from repeat execution only by the in-memory `RequestReplayGuard`), with the owner having had no way to stop it during the valid window.

### Citations

**File:** core/capabilities/vault/allow_list_based_auth.go (L51-76)
```go
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

**File:** core/services/workflows/syncer/v2/workflow_registry.go (L756-775)
```go
			newAllowListedRequests, totalAllowlistedRequests, head, err := w.getAllowlistedRequests(ctx, w.contractReader)
			if err != nil {
				w.lggr.Errorw("failed to call getAllowlistedRequests", "err", err)
				continue
			}
			w.allowListedMu.Lock()
			// Prune expired requests
			activeAllowlistedRequests := []workflow_registry_wrapper_v2.WorkflowRegistryOwnerAllowlistedRequest{}
			expiredRequestsCount := 0
			for _, request := range w.allowListedRequests {
				if int64(request.ExpiryTimestamp) > time.Now().Unix() {
					activeAllowlistedRequests = append(activeAllowlistedRequests, request)
				} else {
					expiredRequestsCount++
				}
			}

			// Add new requests
			activeAllowlistedRequests = append(activeAllowlistedRequests, newAllowListedRequests...)
			w.allowListedRequests = activeAllowlistedRequests
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

**File:** core/services/workflows/syncer/v2/workflow_syncer_v2_test.go (L881-903)
```go
func allowlistRequest(
	t *testing.T,
	th *testutils.EVMBackendTH,
	wfRegC *workflow_registry_wrapper_v2.WorkflowRegistry,
	input allowlistRequestParams,
) {
	t.Helper()
	totalAllowlistedRequestsBefore, err := wfRegC.TotalAllowlistedRequests(&bind.CallOpts{
		From: th.ContractsOwner.From,
	})
	require.NoError(t, err, "failed to get total allowlisted requests")

	requestDigest, err := input.Request.Digest()
	require.NoError(t, err)
	requestDigestBytes, err := hex.DecodeString(requestDigest)
	require.NoError(t, err)

	_, err = wfRegC.AllowlistRequest(
		th.ContractsOwner,
		[32]byte(requestDigestBytes),
		uint32(input.ExpiryTimestamp.Unix()), //nolint:gosec // safe conversion
	)
	require.NoError(t, err, "failed to register allowlisted request")
```
