### Title
Account enumeration via distinguishable error messages in POST /sessions - ([File: core/web/sessions_controller.go], [File: core/sessions/localauth/orm.go])

### Summary
`SessionsController.Create` forwards the raw error returned by `AuthenticationProvider().CreateSession` directly to the client via `jsonAPIError(c, http.StatusUnauthorized, err)`, and the underlying `orm.CreateSession` implementation returns semantically different error values depending on whether the email exists, whether the email case matches, or whether the password is wrong. An unauthenticated caller can therefore distinguish "email does not exist" from "email exists but password is wrong" purely from response content.

### Finding Description
In `core/web/sessions_controller.go`: [1](#0-0) 
the error from `CreateSession` is passed unmodified into `jsonAPIError`, exposing it in the HTTP response body.

In `core/sessions/localauth/orm.go`, `CreateSession` returns different error values along the unauthenticated path:
- If the user does not exist, `o.FindUser` fails and the raw SQL/db error (e.g. "no rows in result set"-style message) is returned unmodified: [2](#0-1) 
- If the email exists but differs only by case/whitespace normalization mismatch, a distinct `"Invalid email"` error is returned: [3](#0-2) 
- If the email matches but the password is wrong, a distinct `"Invalid password"` error is returned: [4](#0-3) 

These three cases produce three different, semantically distinguishable messages, all surfaced verbatim to an unauthenticated caller through `jsonAPIError`. This directly contradicts the intent expressed by the code comment immediately preceding the password check ("Do email and password check first to prevent extra database look up for MFA tokens leaking if an account has MFA tokens or not") — that comment addresses only the MFA-leak vector, not the existence-vs-wrong-password message leak, which remains present.

Separately, `SessionsController.Create` performs its own unconditional `GetUserWebAuthn` lookup before calling `CreateSession`: [5](#0-4) 
This call does not error on a non-existent user (empty result, no error) per `orm.GetUserWebAuthn`: [6](#0-5) 
so this particular call is not itself distinguishable by error status for existing-vs-nonexistent accounts, but it does not mitigate the primary leak described above.

No rate limiting, generic error normalization, or constant-response-shape mechanism exists in `SessionsController.Create` or `orm.CreateSession` to prevent this differential response behavior from being observed by a repeated, unauthenticated caller.

### Impact Explanation
An attacker with only a candidate email address can send `POST /sessions` with an arbitrary password and observe the returned error text to determine whether that email is a registered Core Node user (distinguishing a raw DB "not found" message from the explicit `"Invalid password"` message). This enables account enumeration against admin/API user accounts, which assists targeted credential-stuffing and brute-force campaigns against the Chainlink Node's operator/admin web UI, aligning with the informational-disclosure/account-enumeration class of findings for authentication endpoints.

### Likelihood Explanation
This is trivially and repeatably exploitable: it requires only unauthenticated HTTP access to `POST /sessions`, one guessed email, and any password value. No rate limiting is visible in `SessionsController.Create` to throttle or obscure repeated probing, and the differing error text is deterministic per account state, making it a reliable oracle.

### Recommendation
Normalize all authentication failure responses in `SessionsController.Create`/`orm.CreateSession` to a single generic message and HTTP status (e.g., always `"invalid credentials"` with 401) regardless of whether the failure is due to a nonexistent user, case-mismatched email, or wrong password. Avoid returning underlying database/driver errors to the HTTP layer for `FindUser` failures; instead map them to the same generic error used for wrong-password cases before returning from `orm.CreateSession`.

### Proof of Concept
Unit test in `core/web/sessions_controller_test.go` (or `core/sessions/localauth/orm_test.go`):
1. Seed one user with a known password and no WebAuthn tokens.
2. Call `CreateSession` with: (a) a non-existent email, (b) the seeded email with wrong password, (c) an email that differs only in case from the seeded one, each with an incorrect password.
3. Assert that the three returned error strings are currently distinguishable (`FindUser`'s underlying not-found error vs. `"Invalid email"` vs. `"Invalid password"`), demonstrating the leak.
4. Fixed behavior expectation: all three cases should return an identical generic error string and identical HTTP status via `jsonAPIError`.

### Citations

**File:** core/web/sessions_controller.go (L41-54)
```go
	// Does this user have 2FA enabled?
	userWebAuthnTokens, err := sc.App.AuthenticationProvider().GetUserWebAuthn(ctx, sr.Email)
	if err != nil {
		sc.App.GetLogger().Errorf("Error loading user WebAuthn data: %s", err)
		jsonAPIError(c, http.StatusInternalServerError, errors.New("internal Server Error"))
		return
	}

	// If the user has registered MFA tokens, then populate our session store and context
	// required for successful WebAuthn authentication
	if len(userWebAuthnTokens) > 0 {
		sr.SessionStore = sc.sessions
		sr.WebAuthnConfig = sc.App.GetWebAuthnConfiguration()
	}
```

**File:** core/web/sessions_controller.go (L56-60)
```go
	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
	if err != nil {
		jsonAPIError(c, http.StatusUnauthorized, err)
		return
	}
```

**File:** core/sessions/localauth/orm.go (L130-139)
```go
func (o *orm) GetUserWebAuthn(ctx context.Context, email string) ([]sessions.WebAuthn, error) {
	var uwas []sessions.WebAuthn
	err := o.ds.SelectContext(ctx, &uwas, "SELECT email, public_key_data FROM web_authns WHERE LOWER(email) = $1", strings.ToLower(email))
	if err != nil {
		return uwas, err
	}
	// In the event of not found, there is no MFA on this account and it is not an error
	// so this returns either an empty list or list of WebAuthn rows
	return uwas, nil
}
```

**File:** core/sessions/localauth/orm.go (L144-148)
```go
func (o *orm) CreateSession(ctx context.Context, sr sessions.SessionRequest) (string, error) {
	user, err := o.FindUser(ctx, sr.Email)
	if err != nil {
		return "", err
	}
```

**File:** core/sessions/localauth/orm.go (L152-157)
```go
	// Do email and password check first to prevent extra database look up
	// for MFA tokens leaking if an account has MFA tokens or not.
	if !constantTimeEmailCompare(strings.ToLower(sr.Email), strings.ToLower(user.Email)) {
		o.auditLogger.Audit(audit.AuthLoginFailedEmail, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid email")
	}
```

**File:** core/sessions/localauth/orm.go (L159-162)
```go
	if !utils.CheckPasswordHash(sr.Password, string(user.HashedPassword)) {
		o.auditLogger.Audit(audit.AuthLoginFailedPassword, map[string]any{"email": sr.Email})
		return "", pkgerrors.New("Invalid password")
	}
```
