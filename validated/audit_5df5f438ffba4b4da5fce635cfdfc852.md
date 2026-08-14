### Title
Missing `return` after failed email-claim type assertion allows OIDC session creation with empty `user_email` - ([File: core/sessions/oidcauth/oidc.go])

### Summary
In `handleTokenExchange`, the type assertion `email, ok := claims["email"].(string)` at lines 226-230 writes an error response via `c.String(...)` when the `email` claim is missing or non-string, but does not `return` afterward. Execution falls through with `email == ""` into `IDClaimsToUserRole`, the `oidc_sessions` INSERT, and the final `c.JSON` success response, resulting in a fully authenticated, cookie-backed session bound to an empty `user_email`.

### Finding Description
The token-exchange handler verifies the signed ID token via `oi.provider.Verifier(oi.oidcConfig).Verify(ctx, rawIDToken)` [1](#0-0)  and then unmarshals arbitrary claims into a `map[string]any` [2](#0-1) . Immediately after, the group/role claim is extracted via `ExtractIDClaimValues`, followed by the flawed email extraction:

```go
email, ok := claims["email"].(string)
if !ok {
    oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
    c.String(http.StatusInternalServerError, "Failed to get email from claims")
}
``` [3](#0-2) 

Unlike every other error branch in this function (which all call `return` immediately after writing the error response, e.g. lines 171, 182, 195, 203, 211, 218, 224, 244, 270), this branch omits the `return` statement. Execution therefore continues to:
- `IDClaimsToUserRole(idClaims, ...)` to compute a role from group claims [4](#0-3) 
- an `INSERT INTO oidc_sessions (id, user_email, user_role, created_at)` with `strings.ToLower(email)` where `email` is `""` [5](#0-4) 
- an audit log entry with an empty email [6](#0-5) 
- setting the gin session cookie to the new `clSession.ID` and returning `http.StatusOK` with `Success: true` [7](#0-6) 

The role is derived only from group claims (`idClaims`), which is independent of the email claim, so a role can still be validly resolved (e.g. matching `ReadClaim`/`RunClaim`/etc.) even when `email` is absent. The result is a browser-usable, cookie-authenticated session (`webauth.SessionIDKey`) that maps in `oidc_sessions` to `user_email = ''` and a legitimate role, with the client receiving a `200 OK`/`Success: true` response (the earlier `c.String` write is simply overwritten/duplicated by the later `c.JSON`, so from the client's perspective the flow "succeeds").

Downstream, `AuthorizedUserWithSession` trusts `oidc_sessions.user_email` as the authenticated identity for any request bearing that session cookie [8](#0-7) , and `ClearNonCurrentSessions` performs `lower(user_email) = lower($1)` matching that would now match every other session that also happens to have an empty `user_email` [9](#0-8) . This violates the invariant that no session should be created for an unresolved/invalid identity.

### Impact Explanation
An authenticated session with a blank `user_email` is created and accepted by `AuthorizedUserWithSession`, meaning any request using that session cookie is treated as an authenticated user with a mapped role (e.g. run/edit/admin, depending on group claim matching) but with no accountable identity. If more than one such empty-identity session is ever created, `ClearNonCurrentSessions` invoked by any of them would delete/cross-affect the other's sessions since it matches on `lower(user_email)`, which is empty for both. This is an authentication/session-integrity defect allowing role-scoped access with an unidentifiable/blank principal, and is a real audit-log/accountability failure at minimum, plus session-management collision.

### Likelihood Explanation
This requires the ID token, once cryptographically verified against the configured OIDC provider, to lack a string `email` claim while still containing a group claim matching one of the configured RBAC group names (`AdminClaim`/`EditClaim`/`RunClaim`/`ReadClaim`). This is plausible whenever the upstream IdP does not always emit the `email` claim (e.g., email scope not granted/consented, or provider omits it for certain account types) while still returning group memberships — a scenario within reach of any legitimately-authenticating IdP user who completes the standard `/oidc-login` → `/oidc-exchange` flow, without needing any additional node-operator privilege.

### Recommendation
Add a `return` statement immediately after the `c.String(http.StatusInternalServerError, "Failed to get email from claims")` call in the `!ok` branch at line 229-230, mirroring every other error branch in `handleTokenExchange`, so that execution never proceeds to `IDClaimsToUserRole`/session creation when the email claim is missing or malformed.

### Proof of Concept
Unit test plan (Go, using a fake `sqlutil.DataSource`/DB stub and a stub OIDC provider/verifier that returns claims without an `email` key but with a valid group claim):
1. Construct a `claims` map containing a valid group claim (e.g. `oi.config.ReadClaim()`) but no `"email"` key.
2. Invoke the equivalent of the post-verification logic of `handleTokenExchange` (or refactor the tail of the function into an exported/testable helper, e.g. `finishLogin(claims, idClaims)`... if not already testable, drive it through an httptest server calling `POST /oidc-exchange` with a mocked `oauth2Config.Exchange`/`provider.Verifier` returning the crafted claims).
3. Assert that:
   - No row is inserted into `oidc_sessions` with `user_email = ''` (mock DB should record zero `ExecContext` calls for the INSERT after the missing-email branch).
   - The HTTP response is written exactly once, with a `5xx`/error status and `Success: false`, and no `Set-Cookie`/session establishment occurs.
   - Currently, the test would fail: the mock DB records an `INSERT INTO oidc_sessions ... user_email=''` call, and the final response is `200 OK` with `Success: true`, demonstrating the missing-`return` defect.

### Citations

**File:** core/sessions/oidcauth/oidc.go (L206-212)
```go
	// Verify claim and retrieve attested user id claims
	idToken, err := oi.provider.Verifier(oi.oidcConfig).Verify(ctx, rawIDToken)
	if err != nil {
		oi.lggr.Errorf("Failed to verify ID token: %v", err)
		c.String(http.StatusInternalServerError, "Failed to verify ID token")
		return
	}
```

**File:** core/sessions/oidcauth/oidc.go (L214-219)
```go
	var claims map[string]any
	if err = idToken.Claims(&claims); err != nil {
		oi.lggr.Errorf("Failed to parse OIDC return claims: %v", err)
		c.String(http.StatusInternalServerError, "Failed to parse OIDC return claims")
		return
	}
```

**File:** core/sessions/oidcauth/oidc.go (L226-230)
```go
	email, ok := claims["email"].(string)
	if !ok {
		oi.lggr.Errorf("Failed to get email from claims. error: %v", err)
		c.String(http.StatusInternalServerError, "Failed to get email from claims")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L233-245)
```go
	// Map the claims to a role and insert a newly created session paired with role mapping for user
	role, err := oi.IDClaimsToUserRole(
		idClaims,
		oi.config.AdminClaim(),
		oi.config.EditClaim(),
		oi.config.RunClaim(),
		oi.config.ReadClaim(),
	)
	if err != nil {
		oi.lggr.Errorf("Failed to map configured RBAC role name against received list of group claims: %v", err)
		c.String(http.StatusBadRequest, "No matching role within attested user group claims")
		return
	}
```

**File:** core/sessions/oidcauth/oidc.go (L247-260)
```go
	// Save new user authenticated clSession and role to oidc_sessions table
	// Sessions are set to expire after the duration + creation date elapsed
	clSession := clsessions.NewSession()
	_, err = oi.ds.ExecContext(
		ctx,
		"INSERT INTO oidc_sessions (id, user_email, user_role, created_at) VALUES ($1, $2, $3, now())",
		clSession.ID,
		strings.ToLower(email),
		role,
	)
	if err != nil {
		oi.lggr.Errorf("unable to create new session in oidc_sessions table %v", err)
		c.String(http.StatusInternalServerError, "Error creating session")
	}
```

**File:** core/sessions/oidcauth/oidc.go (L262-262)
```go
	oi.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": email})
```

**File:** core/sessions/oidcauth/oidc.go (L264-276)
```go
	// save session
	ginSession.Set(webauth.SessionIDKey, clSession.ID)
	err = ginSession.Save()
	if err != nil {
		oi.lggr.Errorf("failed to saved session %v", err)
		c.String(http.StatusInternalServerError, "Authentication failed")
		return
	}

	c.JSON(http.StatusOK, ExchangeTokenResponse{
		Success: true,
	})
}
```

**File:** core/sessions/oidcauth/oidc.go (L351-391)
```go
func (oi *oidcAuthenticator) AuthorizedUserWithSession(ctx context.Context, sessionID string) (clsessions.User, error) {
	if len(sessionID) == 0 {
		return clsessions.User{}, errors.New("session ID cannot be empty")
	}
	var foundUser clsessions.User
	err := sqlutil.TransactDataSource(ctx, oi.ds, nil, func(tx sqlutil.DataSource) error {
		// Query the oidc_sessions table for given session ID, user role and email are saved after the id claims is provided and validated
		var foundSession struct {
			UserEmail string
			UserRole  clsessions.UserRole
			Valid     bool
		}
		if err := tx.GetContext(ctx, &foundSession,
			"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM oidc_sessions WHERE id = $1",
			sessionID, oi.config.SessionTimeout().Duration(),
		); err != nil {
			if errors.Is(err, sql.ErrNoRows) {
				return clsessions.ErrUserSessionExpired
			}
			return err
		}
		if !foundSession.Valid {
			// Sessions expired, purge
			return clsessions.ErrUserSessionExpired
		}
		foundUser = clsessions.User{
			Email: foundSession.UserEmail,
			Role:  foundSession.UserRole,
		}
		return nil
	})
	if err != nil {
		if errors.Is(err, clsessions.ErrUserSessionExpired) {
			if _, execErr := oi.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE id = $1", sessionID); execErr != nil {
				oi.lggr.Errorf("error purging stale OIDC session: %v", execErr)
			}
		}
		return clsessions.User{}, err
	}
	return foundUser, nil
}
```

**File:** core/sessions/oidcauth/oidc.go (L442-449)
```go
func (oi *oidcAuthenticator) ClearNonCurrentSessions(ctx context.Context, sessionID string) error {
	var email string
	if err := oi.ds.GetContext(ctx, &email, "SELECT user_email FROM oidc_sessions WHERE id = $1", sessionID); err != nil {
		return err
	}
	_, err := oi.ds.ExecContext(ctx, "DELETE FROM oidc_sessions WHERE lower(user_email) = lower($1) AND id != $2", email, sessionID)
	return err
}
```
