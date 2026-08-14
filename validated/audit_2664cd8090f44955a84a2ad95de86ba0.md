### Title
JWT expiry check validates `exp - iat` delta instead of the absolute lifetime from "now", allowing the token issuer to extend the effective token lifetime beyond `maxExpiryDuration` - (File: `core/utils/jwt.go`)

### Summary
`VerifyRequestJWT` in `core/utils/jwt.go` enforces the maximum allowed lifetime of a self-signed request JWT by computing `duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)` and rejecting only if `duration > maxExpiryDuration` [1](#0-0) . This mirrors the reported Move bug class: the cap is enforced on the *relative delta between two caller-supplied fields* (`exp` and `iat`) rather than on the *absolute distance from the actual current time* to `exp`. Because the signer of the request JWT fully controls both `iat` and `exp` (it is a self-signed ETH-signature JWT, not an externally-issued Auth0 token), an entity presenting requests can set `iat` up to `issuedAtTolerance` in the future and `exp = iat + maxExpiryDuration`, giving a real token lifetime (measured from actual "now") of `issuedAtTolerance + maxExpiryDuration` instead of the intended `maxExpiryDuration` cap — up to roughly double the configured maximum with the current defaults (`maxJWTExpiryDuration = 5m`, `defaultIssuedAtTolerance = 5m`) [2](#0-1) .

### Finding Description
`CreateRequestJWT`/`VerifyRequestJWT` implement a self-signed JWT authentication scheme where the caller signs its own claims with an ECDSA/ETH key [3](#0-2) . On verification:
- `issuedAt` is only checked to not be "too far in the future" relative to `now`, bounded by `issuedAtTolerance` [4](#0-3) .
- The lifetime cap check uses `exp.Sub(iat)` compared against `maxExpiryDuration`, not `exp.Sub(now)` [1](#0-0) .

Because both `iat` and `exp` are chosen and signed by the same party presenting the token, this is structurally identical to the reported `name_service.move` flaw: the validation subtracts two attacker/self-controlled values instead of bounding the value against the trusted reference point (there: `timestamp`/now; here: `now`). A caller can therefore craft `iat = now + issuedAtTolerance` and `exp = iat + maxExpiryDuration`, passing both checks while the token remains valid for `issuedAtTolerance + maxExpiryDuration` from the real current time — exceeding the intended policy cap.

### Impact Explanation
The `maxExpiryDuration` policy exists to bound how long a signed authorization for a specific request digest remains valid (used together with `RequestReplayGuard` for replay protection, see the leeway/expiry wiring in `jwt_based_auth.go`'s companion `RequestReplayGuard` and the JWT test suite validating "token lifetime ... exceeds the maximum allowed" behavior) [5](#0-4) . Extending the effective lifetime beyond the configured cap widens the window during which a captured/leaked token or a pre-signed request can be replayed or remain authoritative, undermining the freshness guarantee the expiry check is meant to provide. The severity is bounded (this doubles the window rather than making it unbounded), since `issuedAtTolerance` itself is a fixed, small value, but it is a concrete violation of the intended maximum-lifetime invariant enforced at an auth-boundary component.

### Likelihood Explanation
Any party capable of producing a signed request JWT for itself (i.e., anyone using the self-signed JWT auth path exercised by `CreateRequestJWT`/`VerifyRequestJWT`) can trivially set `iat` and `exp` to arbitrary values within their signed claims — no special privilege beyond being a normal caller of this authentication mechanism is required, since it only depends on the caller's own signing key. The only constraint is staying within `issuedAtTolerance` for `iat`, which is deliberately generous (default 5 minutes) to tolerate clock skew, making exploitation straightforward and reliable.

### Recommendation
Bound the lifetime check against the actual verification time instead of the caller-controlled `iat`:
```go
duration := verifiedClaims.ExpiresAt.Sub(now)
if duration > maxExpiryDuration {
    return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec", duration.Seconds(), maxExpiryDuration.Seconds())
}
```
This ensures `exp` can never be further than `maxExpiryDuration` from the real current time, regardless of how `iat` is chosen, closing the gap that currently allows the tolerance window to be stacked on top of the intended cap.

### Proof of Concept
Using the existing test harness in `core/utils/jwt_test.go` [5](#0-4) , construct a token where:
```go
now := time.Now()
issuedAt := now.Add(defaultIssuedAtTolerance)      // max allowed future iat
expiresAt := issuedAt.Add(maxJWTExpiryDuration)    // exp - iat == cap, passes check

claims := JWTClaims{
    Digest: "0x" + digest,
    RegisteredClaims: jwt.RegisteredClaims{
        ID:        "test-jti",
        ExpiresAt: jwt.NewNumericDate(expiresAt),
        IssuedAt:  jwt.NewNumericDate(issuedAt),
    },
}
token := jwt.NewWithClaims(&SigningMethodEth{}, claims)
tokenString, _ := token.SignedString(privateKey)

_, _, err := VerifyRequestJWT(tokenString, req)
// err == nil, even though expiresAt is now.Add(defaultIssuedAtTolerance + maxJWTExpiryDuration),
// i.e. ~10 minutes from "now" instead of the intended 5-minute maximum.
```
This demonstrates the real, absolute lifetime (`expiresAt - now`) exceeds `maxJWTExpiryDuration` while the existing check (`exp - iat <= maxJWTExpiryDuration`) still passes.

### Citations

**File:** core/utils/jwt.go (L19-22)
```go
const (
	maxJWTExpiryDuration     = 5 * time.Minute // Maximum allowed expiry duration
	defaultIssuedAtTolerance = 5 * time.Minute // Default tolerance for issuedAt validation to handle clock drift
)
```

**File:** core/utils/jwt.go (L168-216)
```go
func CreateRequestJWT[T any](req jsonrpc.Request[T], opts ...Option) (*jwt.Token, error) {
	// Apply options
	options := &jwtOptions{}
	for _, opt := range opts {
		opt(options)
	}

	expiryDuration := maxJWTExpiryDuration
	if options.expiryDuration != nil {
		expiryDuration = *options.expiryDuration
	}

	digest, err := req.Digest()
	if err != nil {
		return nil, err
	}

	var issuer string
	if options.issuer != nil {
		issuer = *options.issuer
	}

	var subject string
	if options.subject != nil {
		subject = *options.subject
	}

	var audience []string
	if options.audience != nil {
		audience = options.audience
	}

	now := time.Now()
	jti := uuid.New().String()

	claims := JWTClaims{
		Digest: "0x" + digest,
		RegisteredClaims: jwt.RegisteredClaims{
			ID:        jti,
			Issuer:    issuer,
			Subject:   subject,
			Audience:  jwt.ClaimStrings(audience),
			ExpiresAt: jwt.NewNumericDate(now.Add(expiryDuration)),
			IssuedAt:  jwt.NewNumericDate(now),
		},
	}

	return jwt.NewWithClaims(&SigningMethodEth{}, claims), nil
}
```

**File:** core/utils/jwt.go (L290-294)
```go
	now := time.Now()
	issuedAt := verifiedClaims.IssuedAt
	if issuedAt.After(now.Add(issuedAtTolerance)) {
		return nil, gethcommon.Address{}, fmt.Errorf("issuedAt (iat) is too far in the future (beyond tolerance of %.0f seconds)", issuedAtTolerance.Seconds())
	}
```

**File:** core/utils/jwt.go (L295-298)
```go
	duration := verifiedClaims.ExpiresAt.Sub(verifiedClaims.IssuedAt.Time)
	if duration > maxExpiryDuration {
		return nil, gethcommon.Address{}, fmt.Errorf("token lifetime %.0f sec exceeds the maximum allowed %.0f sec. Reduce the gap between 'iat' and 'exp'", duration.Seconds(), maxExpiryDuration.Seconds())
	}
```

**File:** core/utils/jwt_test.go (L393-418)
```go
	t.Run("should validate that expiredAt exceeds max expiry", func(t *testing.T) {
		digest, err := req.Digest()
		require.NoError(t, err)

		now := time.Now()
		issuedAt := now
		expiresAt := now.Add(maxJWTExpiryDuration * 2)

		claims := JWTClaims{
			Digest: "0x" + digest,
			RegisteredClaims: jwt.RegisteredClaims{
				ID:        "test-jti",
				ExpiresAt: jwt.NewNumericDate(expiresAt),
				IssuedAt:  jwt.NewNumericDate(issuedAt),
			},
		}

		token := jwt.NewWithClaims(&SigningMethodEth{}, claims)
		tokenString, err := token.SignedString(privateKey)
		require.NoError(t, err)

		_, _, err = VerifyRequestJWT(tokenString, req)
		require.Error(t, err)
		require.Contains(t, err.Error(), "token lifetime")
		require.Contains(t, err.Error(), "exceeds the maximum allowed")
	})
```
