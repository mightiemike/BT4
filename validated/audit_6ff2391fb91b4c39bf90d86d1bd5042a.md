### Title
User enumeration via authentication timing side-channel in session creation - (File: core/sessions/localauth/orm.go)

### Summary
`(*orm).CreateSession` returns immediately after a failed `FindUser` lookup for a nonexistent email, but for an existing email it proceeds to run `utils.CheckPasswordHash` (bcrypt, `DefaultCost`), which is computationally expensive relative to a DB miss. This creates a measurable timing (and in some paths, response-code) asymmetry that lets an unauthenticated caller distinguish "email does not exist" from "email exists, password wrong."

### Finding Description
In `core/sessions/localauth/orm.go`, `CreateSession` first calls `o.FindUser(ctx, sr.Email)` [1](#0-0) . For a nonexistent email this returns a SQL "no rows" error almost immediately and the function returns without ever touching bcrypt. For an existing email, execution continues to the `constantTimeEmailCompare` check (constant-time, cheap) and then to `utils.CheckPasswordHash(sr.Password, string(user.HashedPassword))` [2](#0-1) , which calls `bcrypt.CompareHashAndPassword` [3](#0-2)  using `bcrypt.DefaultCost` (cost 10) for hashing at creation time [4](#0-3) . bcrypt at cost 10 takes on the order of tens of milliseconds, dwarfing a single indexed DB lookup miss. `constantTimeEmailCompare` only protects the email-comparison step itself from timing leaks (comparing attacker-supplied email against the found user's email), not the much larger asymmetry between "user found → bcrypt executed" vs "user not found → early return." Additionally, before `CreateSession` is even invoked, `SessionsController.Create` performs a `GetUserWebAuthn` DB query for the submitted email [5](#0-4) , but this does not offset the CreateSession-internal bcrypt asymmetry. No rate limiting is visible in `sessions_controller.go`'s `Create` handler to throttle repeated attempts.

### Impact Explanation
This enables an unauthenticated attacker to enumerate valid administrator/API user emails on a Chainlink node's `/sessions` endpoint purely from response timing, without any credentials. This matches a "user enumeration" / information-disclosure class finding, which is typically low severity on its own but is a real building block for follow-on targeted credential-stuffing or password-guessing against confirmed accounts, indirectly increasing risk of unauthorized session creation.

### Likelihood Explanation
Feasible with no preconditions beyond network access to the login endpoint. It requires statistical timing analysis over many repeated trials (standard bcrypt-vs-no-bcrypt timing differentials are well-documented and detectable even over network jitter with enough samples), and no rate-limiting/lockout mechanism is present in the reviewed code to prevent the volume of requests such an attack requires.

### Recommendation
Perform a constant-cost operation on the miss path as well: when `FindUser` fails to find a user, run a dummy/fixed-cost bcrypt comparison against a static hash before returning the "Invalid email" error, so that both branches take comparable time. Alternatively, add rate limiting / exponential backoff per source IP and per candidate email on the `/sessions` create endpoint, and consider returning a generic error/timing-normalized response regardless of whether the email exists.

### Proof of Concept
Integration test plan (Go, using `core/sessions/localauth/orm_test.go` patterns):
1. Seed one known user with a hashed password via `CreateUser`.
2. Loop N (e.g. 200) times calling `CreateSession` with a random nonexistent email + fixed bogus password; record elapsed time for each call.
3. Loop N times calling `CreateSession` with the known existing email + fixed wrong password; record elapsed time for each call.
4. Assert that the mean/median latency of the "existing email" branch is statistically significantly higher (e.g. by tens of milliseconds, consistent with one bcrypt comparison) than the "nonexistent email" branch, confirming the side channel; assert current behavior lacks any compensating delay/backoff.

### Citations

**File:** core/sessions/localauth/orm.go (L144-148)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```

**File:** core/utils/utils.go (L125-129)
```go
// HashPassword wraps around bcrypt.GenerateFromPassword for a friendlier API.
func HashPassword(password string) (string, error) {
	bytes, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	return string(bytes), err
}
```

**File:** core/utils/utils.go (L131-135)
```go
// CheckPasswordHash wraps around bcrypt.CompareHashAndPassword for a friendlier API.
func CheckPasswordHash(password, hash string) bool {
	err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(password))
	return err == nil
}
```

**File:** core/web/sessions_controller.go (L41-47)
```go
	// Does this user have 2FA enabled?
	userWebAuthnTokens, err := sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)
	if err != nil {
		sc.App.GetLogger().Errorf("Error loading user WebAuthn data: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
	}
```
