### Title
Timing side-channel in `localLoginFallback` allows local admin email enumeration - ([File: core/sessions/oidcauth/oidc.go])

### Summary
`localLoginFallback` returns immediately when `oi.ds.GetContext(ctx, &user, SQLSelectUserbyEmail, sr.Email)` fails to find a user, skipping both `constantTimeEmailCompare` and `utils.CheckPasswordHash`. Since `CheckPasswordHash` performs a deliberately slow bcrypt comparison, requests for existing emails take measurably longer than requests for non-existing emails, letting an unauthenticated attacker enumerate valid local admin accounts via `POST /sessions`.

### Finding Description
`CreateSession` (`core/sessions/oidcauth/oidc.go:412`) calls `localLoginFallback` (`core/sessions/oidcauth/oidc.go:580-597`) for every login attempt hitting `/sessions` (wired via `core/web/sessions_controller.go`). The flow is:

```go
func (oi *oidcAuthenticator) localLoginFallback(ctx context.Context, sr clsessions.SessionRequest) (clsessions.User, error) {
	var user clsessions.User
	err := oi.ds.GetContext(ctx, &user, SQLSelectUserbyEmail, sr.Email)
	if err != nil {
		return user, err   // <-- early return on unknown email, skips comparisons below
	}
	if !constantTimeEmailCompare(...) { ... }
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) { ... }
	...
}
``` [1](#0-0) 

For an unknown email, the function returns as soon as the DB query yields `sql.ErrNoRows` — a fast, cheap failure path. For a known email with a wrong password, execution proceeds to `constantTimeEmailCompare` (cheap) and then `utils.CheckPasswordHash` (bcrypt), which is intentionally computationally expensive (typically single-digit to double-digit milliseconds depending on cost factor). This asymmetry produces a measurable latency gap between the "unknown email" and "known email, wrong password" cases. `constantTimeEmailCompare` at line 648-656 only protects against a *content* comparison timing leak (comparing string bytes), but does nothing to equalize the *control-flow* timing difference caused by skipping the bcrypt hash step entirely when the DB lookup misses. [2](#0-1) 

No rate limiting, dummy-hash comparison, or constant-time floor is applied to the miss path, so the invariant "authentication failure paths must be constant-time regardless of account existence" is violated.

### Impact Explanation
This enables an unauthenticated attacker to enumerate valid local admin email addresses on the `/sessions` local login fallback endpoint (used even when OIDC is the primary auth mechanism), by statistically distinguishing "unknown email" (fast, no bcrypt) from "known email" (slow, bcrypt executed) responses. This is a user/email enumeration vulnerability that facilitates targeted credential stuffing/brute-force/social-engineering attacks against local admin fallback accounts — a legitimate, scoped information-disclosure impact reachable from an ordinary unauthenticated web request.

### Likelihood Explanation
No special privileges are required — only network access to the login endpoint. Bcrypt's cost factor makes the timing gap large and reliably measurable with a modest number of samples (statistical averaging easily overcomes network jitter). The attack is fully repeatable and scriptable against arbitrary email guesses.

### Recommendation
Ensure the failure path performs equivalent work regardless of whether the account exists: on `sql.ErrNoRows`, execute a dummy `utils.CheckPasswordHash` call against a precomputed fixed/dummy bcrypt hash before returning the error, so total latency is independent of user existence. Alternatively, always perform the DB lookup, dummy-hash comparison, and error dispatch through a uniform code path with constant total execution time (e.g., padding to a fixed minimum duration).

### Proof of Concept
Unit/benchmark test in `core/sessions/oidcauth/oidc_test.go`:
1. Seed one known user with a bcrypt-hashed password.
2. Run N iterations of `localLoginFallback` with random non-existent emails and record latency distribution.
3. Run N iterations of `localLoginFallback` with the known email and a wrong password and record latency distribution.
4. Assert the two distributions' means differ by more than a small epsilon (demonstrating the timing gap), e.g., using `testing.B` benchmarks or manual `time.Now()` deltas averaged over hundreds of runs, showing the "known email" path is consistently slower by roughly the bcrypt comparison cost (e.g., several milliseconds), while a fixed/dummy-hash mitigation would bring both distributions within a bounded epsilon (e.g., <1ms difference).

### Citations

**File:** core/sessions/oidcauth/oidc.go (L580-597)
```go
func (oi *oidcAuthenticator) localLoginFallback(ctx context.Context, sr clsessions.SessionRequest) (clsessions.User, error) {
	var user clsessions.User
	err := oi.ds.GetContext(ctx, &user, SQLSelectUserbyEmail, sr.Email)
	if err != nil {
		return user, err
	}
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return user, errors.New("invalid email")
	}

	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		oi.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return user, errors.New("invalid password")
	}

	return user, nil
}
```

**File:** core/sessions/oidcauth/oidc.go (L648-656)
```go
func constantTimeEmailCompare(left, right string) bool {
	const constantTimeEmailLength = 256
	length := mathutil.Max(constantTimeEmailLength, len(left), len(right))
	leftBytes := make([]byte, length)
	rightBytes := make([]byte, length)
	copy(leftBytes, left)
	copy(rightBytes, right)
	return subtle.ConstantTimeCompare(leftBytes, rightBytes) == 1
}
```
