### Title
JWT lifetime cap can be bypassed via future-dated `iat`, extending Vault authorization validity beyond the intended maximum - (File: `core/utils/jwt.go`)

### Summary
`VerifyRequestJWT` enforces a maximum token lifetime by comparing the `exp` claim against the `iat` claim (`duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)`) rather than measuring the remaining time from the actual verification moment (`time.Now()`). Because the token issuer also controls `iat`, and the verifier separately tolerates an `iat` up to `issuedAtTolerance` (default 5 minutes) in the future, an attacker-crafted token can produce a real validity window (relative to true wall-clock time) that exceeds the intended `maxJWTExpiryDuration` cap. This mirrors the Royco `VaultKernel.sol` bug: a duration/limit check computed from two claim-supplied timestamps (`end - start`) instead of against the current time (`end - now`).

### Finding Description
`CreateRequestJWT`/`VerifyRequestJWT` are meant to bound how long a signed request-authorization token can remain valid, capped at `maxJWTExpiryDuration` (5 minutes) [1](#0-0) .

In `VerifyRequestJWT`, the future-dating tolerance check and lifetime cap check are performed independently and both use claim-relative timestamps instead of anchoring to `time.Now()` for the lifetime measurement: [2](#0-1) 

- `issuedAt` may legally be up to `issuedAtTolerance` (default 5 min) ahead of `time.Now()`.
- The lifetime check only bounds `exp - iat` to `maxExpiryDuration` (default 5 min), not `exp - time.Now()`.

Combining these, a token can be constructed with `iat = now + 5min` and `exp = iat + 5min = now + 10min`. Both checks pass (`iat` is within tolerance of now; `exp - iat` is within the 5-minute cap), yet the token remains valid for 10 minutes from the actual current time — double the intended maximum lifetime — because the cap was computed relative to the (attacker-controlled) `iat` rather than relative to the true current time, exactly analogous to the reported `end - start` vs. `end - block.timestamp` flaw.

The downstream Vault authorization result is directly tied to this attacker-influenced `exp` value: `AuthResult.ExpiresAt()` is derived from `verifiedClaims.ExpiresAt` (plus a validation leeway), not recomputed from a bounded server-side clock, as shown by the corresponding test [3](#0-2) .

### Impact Explanation
Any party able to construct and sign their own request-authorization JWT (a normal, unprivileged capability-requester flow — the JWT is created client-side via `CreateRequestJWT` and merely verified server-side) can extend the effective authorization/replay-protection window for a privileged Vault request beyond the policy-mandated maximum lifetime. This weakens a security control intended to limit the blast radius of a leaked or intercepted token and to bound how long a digest-authorized request stays valid/replay-detectable, which is directly relevant to the Vault's auth/session trust boundary.

### Likelihood Explanation
The bypass requires only crafting/signing a JWT with a future `iat` within the default 5-minute tolerance and an `exp` within 5 minutes of that `iat` — both fully under attacker control at token creation time, since `iat`/`exp` are self-declared claims. No special privileges, race conditions, or node compromise are needed to trigger the flawed comparison; it is reachable through the standard `VerifyRequestJWT` code path used to authorize Vault requests.

### Recommendation
Compute the lifetime check against the true current time rather than the claim-relative offset, e.g.:
- Reject if `exp.Sub(time.Now()) > maxExpiryDuration` (bound remaining validity from now), in addition to (not instead of) any `iat`/`exp` ordering sanity check, and/or
- Reduce/remove the additive effect of `issuedAtTolerance` when computing the effective cap, so total possible validity from "true now" can never exceed `maxExpiryDuration`.

### Proof of Concept
1. Attacker calls `CreateRequestJWT`-equivalent logic manually (or crafts the payload directly) setting:
   - `iat = time.Now() + 4m59s` (just under `issuedAtTolerance` = 5m)
   - `exp = iat + 4m59s` (just under `maxExpiryDuration` = 5m), i.e., `exp ≈ time.Now() + 9m58s`
2. Signs the token with `SigningMethodEth`.
3. Calls `VerifyRequestJWT`:
   - `issuedAt.After(now.Add(issuedAtTolerance))` → false, passes.
   - `duration := exp.Sub(iat)` ≈ 4m59s ≤ `maxExpiryDuration` (5m) → passes.
4. Resulting `AuthResult.ExpiresAt()` (from `verifiedClaims.ExpiresAt`) is valid for ~10 minutes from the real current time, double the intended 5-minute maximum token lifetime [2](#0-1) .

### Citations

**File:** core/utils/jwt.go (L19-22)
```go
const (
	maxJWTExpiryDuration     = 5 * time.Minute // Maximum allowed expiry duration
	defaultIssuedAtTolerance = 5 * time.Minute // Default tolerance for issuedAt validation to handle clock drift
)
```

**File:** core/utils/jwt.go (L290-298)
```go
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
```

**File:** core/capabilities/vault/jwt_based_auth_test.go (L220-259)
```go
func TestJWTBasedAuth_AuthResultExpiryIncludesValidationLeeway(t *testing.T) {
	rsaKey := generateTestRSAKey(t, "key-1")
	jwksServer := newTestJWKSServer(t, rsaKey)

	issuer := jwksServer.URL() + "/"
	audience := "https://vault.test.chain.link"
	v := newTestValidator(t, issuer, audience)

	derivedOrg123Owner := testJWTExpectedWorkflowOwner(t, 1, "org-123")
	rawRequest := fmt.Appendf(nil, `{"jsonrpc":"2.0","id":"req-1","method":"vault.secrets.list","params":{"request_id":"req-1","owner":"%s","namespace":"main"}}`, derivedOrg123Owner)
	req, err := jsonrpc.DecodeRequest[json.RawMessage](rawRequest, "")
	require.NoError(t, err)

	digest, err := req.Digest()
	require.NoError(t, err)

	tokenExp := time.Now().Add(2 * time.Minute).Truncate(time.Second)
	token := createTestJWT(t, rsaKey, jwt.MapClaims{
		"iss":                             issuer,
		"aud":                             audience,
		"exp":                             jwt.NewNumericDate(tokenExp),
		"iat":                             jwt.NewNumericDate(time.Now()),
		"org_id":                          "org-123",
		ClaimVaultSecretManagementEnabled: "true",
		ClaimChainlinkTenantID:            "1",
		"scope":                           OAuthScopeVaultSecretsList,
		"authorization_details": []any{
			map[string]any{
				"type":  "request_digest",
				"value": digest,
			},
		},
	})

	req, err = jsonrpc.DecodeRequest[json.RawMessage](rawRequest, token)
	require.NoError(t, err)

	authResult, err := v.AuthorizeRequest(t.Context(), req)
	require.NoError(t, err)
	require.Equal(t, tokenExp.UTC().Add(time.Minute).Unix(), authResult.ExpiresAt())
```
