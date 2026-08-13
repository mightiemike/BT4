### Title
Timing side-channel in `AuthenticateExternalInitiator`/`FindExternalInitiator` may permit External Initiator AccessKey enumeration - ([File: core/web/auth/auth.go], [File: core/bridges/external_initiator.go], [File: core/auth/auth.go])

### Summary
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` short-circuits with `auth.ErrorAuthFailed` immediately when `FindExternalInitiator` returns `sql.ErrNoRows`, but when the `access_key` does exist it proceeds to compute `auth.HashedSecret` (a SHA3-256 hash) and then run `subtle.ConstantTimeCompare` before failing. `subtle.ConstantTimeCompare` itself is constant-time over the comparison, but it only protects the comparison step — it does nothing to hide the fact that the "found" code path performs strictly more work (a DB row materialization plus a SHA3-256 hash) than the "not found" path.

### Finding Description
The relevant call chain is: [1](#0-0) . When `store.FindExternalInitiator` (backed by `SELECT * FROM external_initiators WHERE access_key = $1`, [2](#0-1) ) returns `sql.ErrNoRows`, the function returns `auth.ErrorAuthFailed` immediately without ever calling `bridges.AuthenticateExternalInitiator`. If the `access_key` exists, execution proceeds into `bridges.AuthenticateExternalInitiator`, which calls `auth.HashedSecret` (writing to and summing a `sha3.New256` hasher, [3](#0-2) ) and then `subtle.ConstantTimeCompare` [4](#0-3) . `ConstantTimeCompare` eliminates timing leakage based on *where* the secrets first differ, but it does not — and cannot — equalize the cost difference between "row not found, return early" and "row found, hash + compare, then fail."

### Impact Explanation
If exploitable, this would let an unprivileged attacker distinguish valid vs. invalid `AccessKey` values by response latency, without needing any credentials, enabling enumeration of valid External Initiator `AccessKey`s prior to brute-forcing/guessing the corresponding `Secret`. This matches a low-severity "information disclosure via timing side channel" class of finding rather than a direct auth bypass, since the `Secret` itself remains protected by constant-time comparison and (in `AuthenticateByToken`'s case) a random 64-byte secret.

### Likelihood Explanation
The theoretical asymmetry exists, but practical exploitability is weak: the extra work added by the "found" path is a single SHA3-256 hash over a short (~100 byte) input, which executes in the sub-microsecond-to-low-microsecond range, while both code paths still perform a database round trip on an indexed lookup of `access_key` (`SELECT * FROM external_initiators WHERE access_key = $1`). Over a real network, DB connection pooling, query planner variance, GC pauses, and network jitter are typically orders of magnitude larger (milliseconds) than the SHA3-256 computation delta, making the signal very hard to isolate without an extremely large number of samples and low-noise conditions (e.g., attacker colocated with the node). No rate limiting is visible in this file to prevent high-volume probing, which somewhat helps large-sample statistical attacks, but the underlying signal-to-noise ratio over a normal API path is poor. This is a real but low-likelihood/low-severity structural issue rather than a readily reproducible oracle.

### Recommendation
Perform the `HashedSecret` computation and `ConstantTimeCompare` unconditionally, even when the `AccessKey` is not found (compare against a fixed/dummy salted hash in that case), so both code paths do equivalent cryptographic work regardless of whether the `access_key` exists. Additionally, consider adding rate limiting/backoff on external-initiator and API-token authentication endpoints to reduce the number of samples an attacker can gather for any timing-based enumeration attempt.

### Proof of Concept
Statistical timing-differential test (integration-level, run locally against a real DB to reduce network noise):
1. Insert one `ExternalInitiator` row via `MustInsertExternalInitiatorWithOpts` ( [5](#0-4) ) with a known `AccessKey`.
2. For N trials (e.g., N=10,000), call `store.FindExternalInitiator` + `bridges.AuthenticateExternalInitiator` (or hit the `AuthenticateExternalInitiator` gin middleware directly) with:
   - Group A: a random, non-existent `AccessKey` (expect `sql.ErrNoRows` fast path).
   - Group B: the real `AccessKey` with a wrong `Secret` (expect hash+compare path).
3. Record wall-clock latency for each call using `time.Now()`/`time.Since()` bracketing only the authentication call (excluding HTTP overhead) to minimize noise.
4. Compute mean/median and run a Mann-Whitney U test or t-test comparing Group A vs Group B latencies.
5. Expected assertion for the finding to be confirmed: statistically significant (p < 0.01) latency difference between Group A and Group B in a low-noise local environment, demonstrating the structural (not comparison-based) timing gap; a corresponding unit test would assert that `FindExternalInitiator`+`AuthenticateExternalInitiator` for a non-existent key never calls `auth.HashedSecret`, confirmed via instrumentation/mocking of `bridges.ORM.FindExternalInitiator`.

### Citations

**File:** core/web/auth/auth.go (L119-141)
```go
func AuthenticateExternalInitiator(c *gin.Context, store Authenticator) error {
	ctx := c.Request.Context()
	eia := &auth.Token{
		AccessKey: c.GetHeader(static.ExternalInitiatorAccessKeyHeader),
		Secret:    c.GetHeader(static.ExternalInitiatorSecretHeader),
	}

	ei, err := store.FindExternalInitiator(ctx, eia)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return auth.ErrorAuthFailed
		}

		return errors.Wrap(err, "finding external initiator")
	}

	ok, err := bridges.AuthenticateExternalInitiator(eia, ei)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}
```

**File:** core/bridges/orm.go (L262-267)
```go
// FindExternalInitiator finds an external initiator given an authentication request
func (o *orm) FindExternalInitiator(ctx context.Context, eia *auth.Token) (*ExternalInitiator, error) {
	exi := &ExternalInitiator{}
	err := o.ds.GetContext(ctx, exi, `SELECT * FROM external_initiators WHERE access_key = $1`, eia.AccessKey)
	return exi, err
}
```

**File:** core/auth/auth.go (L55-64)
```go
// HashedSecret generates a hashed password for an external initiator
// authentication
func HashedSecret(ta *Token, salt string) (string, error) {
	hasher := hash.Hash(sha3.New256())
	_, err := hasher.Write(hashInput(ta, salt))
	if err != nil {
		return "", pkgerrors.Wrap(err, "error writing external initiator authentication to hasher")
	}
	return hex.EncodeToString(hasher.Sum(nil)), nil
}
```

**File:** core/bridges/external_initiator.go (L61-67)
```go
func AuthenticateExternalInitiator(eia *auth.Token, ea *ExternalInitiator) (bool, error) {
	hashedSecret, err := auth.HashedSecret(eia, ea.Salt)
	if err != nil {
		return false, err
	}
	return subtle.ConstantTimeCompare([]byte(hashedSecret), []byte(ea.HashedSecret)) == 1, nil
}
```

**File:** core/internal/cltest/factories.go (L210-230)
```go
func MustInsertExternalInitiatorWithOpts(t *testing.T, orm bridges.ORM, opts ExternalInitiatorOpts) (ei bridges.ExternalInitiator) {
	ctx := t.Context()
	var prefix string
	if opts.NamePrefix != "" {
		prefix = opts.NamePrefix
	} else {
		prefix = "ei"
	}
	ei.Name = fmt.Sprintf("%s-%s", prefix, uuid.New())
	ei.URL = opts.URL
	ei.OutgoingSecret = opts.OutgoingSecret
	ei.OutgoingToken = opts.OutgoingToken
	token := auth.NewToken()
	ei.AccessKey = token.AccessKey
	ei.Salt = utils.NewSecret(utils.DefaultSecretSize)
	hashedSecret, err := auth.HashedSecret(token, ei.Salt)
	require.NoError(t, err)
	ei.HashedSecret = hashedSecret
	err = orm.CreateExternalInitiator(ctx, &ei)
	require.NoError(t, err)
	return ei
```
