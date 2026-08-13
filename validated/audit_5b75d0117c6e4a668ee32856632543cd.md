### Title
LDAP unauthenticated-bind authentication bypass via empty password in `CreateSession` - ([File: core/sessions/ldapauth/ldap.go])

### Summary
`ldapAuthenticator.CreateSession` passes the attacker-supplied `sr.Password` directly to `conn.Bind(searchBaseDN, sr.Password)` without ever validating that the password is non-empty. Per RFC 4513 §5.1.2, an LDAP simple bind with a valid DN and a zero-length password is defined as an "unauthenticated bind," which many LDAP/AD servers return as success (no error) rather than as an authentication failure, unless the server operator has explicitly disabled unauthenticated binds. This lets an unprivileged attacker who only knows a valid user's email create an authenticated Chainlink session without knowing that user's password.

### Finding Description
In `CreateSession` [1](#0-0) , the code builds the DN from `sr.Email` and calls `conn.Bind(searchBaseDN, sr.Password)` with no check that `sr.Password` is non-empty. The `LDAPConn.Bind` method is a thin wrapper directly over `go-ldap`'s `*ldap.Conn.Bind` [2](#0-1) , which performs a standard LDAP simple bind. Per the LDAP protocol (RFC 4513 §5.1.2), a simple bind with a non-empty DN and an empty password is an "unauthenticated bind," and by default many directory servers accept it as successful rather than reject it (this is a well-known LDAP protocol pitfall, not a Chainlink-specific quirk).

The HTTP entry point `SessionsController.Create` binds the request JSON straight into `sessions.SessionRequest` and forwards it to `AuthenticationProvider().CreateSession(ctx, sr)` with no server-side validation that `password` is non-empty [3](#0-2) . `SessionRequest.Password` is a plain string field with no "required" or non-empty constraint [4](#0-3) .

If the bind against an LDAP server configured to allow unauthenticated binds succeeds with an empty password, `err` from `conn.Bind` is `nil`, so `returnErr` remains `nil`, and execution proceeds to `FindUser` to fetch the user's role and then unconditionally creates and persists a new session row in `ldap_sessions` for that email/role [5](#0-4) . This grants a full authenticated session (with the target user's real RBAC role, potentially `Admin`) with zero credential knowledge beyond the target's email address, which is often guessable/enumerable (e.g., corporate email conventions).

Existing checks that fail to stop this:
- No pre-check on `sr.Password == ""` before calling `conn.Bind`.
- `constantTimeEmailCompare`/password-hash checks exist only in the `localLoginFallback`/local-auth path (`core/sessions/localauth/orm.go`), not in the primary LDAP bind path.
- Nothing in `SessionsController.Create` validates the request body beyond JSON shape.

This is contingent on the LDAP server's configuration (must permit unauthenticated binds, which is default in many OpenLDAP/389-DS deployments unless `olcDisallows: bind_anon` or equivalent is set), so it is an application-level defense-in-depth gap that trusts the directory server to reject empty passwords rather than enforcing it itself — a classic CWE-521/CWE-287 pattern for LDAP-integrated applications.

### Impact Explanation
If exploited against an LDAP backend permitting unauthenticated binds, an unprivileged remote attacker can authenticate as any known LDAP user — including admin-group members — without their password, gaining a valid Chainlink node session cookie with that user's full RBAC role (up to Admin). This is a direct authentication bypass / full account takeover, matching Chainlink's bounty category for authentication bypass leading to unauthorized privileged access on the node's web/API surface.

### Likelihood Explanation
Preconditions: (1) the node operator has enabled the LDAP authentication driver, and (2) the upstream LDAP/AD server has not explicitly disabled unauthenticated binds (a non-default hardening step many operators omit). Given those preconditions, the attack requires only knowledge of a valid user email (often derivable from corporate naming conventions or leaked from elsewhere) and a single unauthenticated HTTP POST to `/sessions` with an empty `password` field — no rate-limiting bypass, no privileged access, fully repeatable.

### Recommendation
Reject empty (or whitespace-only) passwords in `CreateSession` (and `TestPassword`) before calling `conn.Bind`, e.g.:
```go
if sr.Password == "" {
    return "", errors.New("password must not be empty")
}
```
Apply the same guard in `TestPassword` [6](#0-5) . Additionally, consider validating this centrally in `SessionsController.Create` for all authentication drivers.

### Proof of Concept
Unit test extending `core/sessions/ldapauth/ldap_test.go`:
```go
func TestORM_CreateSession_EmptyPasswordUnauthenticatedBind(t *testing.T) {
    t.Parallel()
    ctx := t.Context()

    mockLdapClient := mocks.NewLDAPClient(t)
    mockLdapConnProvider := mocks.NewLDAPConn(t)
    mockLdapClient.On("CreateEphemeralConnection").Return(mockLdapConnProvider, nil)
    mockLdapConnProvider.On("Close").Return(nil)

    // Simulate an LDAP server that treats empty-password bind as an
    // "unauthenticated bind" success (per RFC 4513 5.1.2), returning nil error
    mockLdapConnProvider.On("Bind", mock.Anything, "").Return(nil).Once()

    _, ldapAuthProvider := setupAuthenticationProvider(t, mockLdapClient)
    // ... mock Search calls in FindUser to resolve group/role for cltest.APIEmailAdmin

    sessionRequest := sessions.SessionRequest{
        Email:    cltest.APIEmailAdmin,
        Password: "",
    }

    sessionID, err := ldapAuthProvider.CreateSession(ctx, sessionRequest)
    // Expected (fixed) behavior: error, no session
    require.Error(t, err)
    require.Empty(t, sessionID)
    // Current (vulnerable) behavior: err is nil and a valid session ID is returned,
    // demonstrating authentication bypass with empty password.
}
```
This test currently would need the fake `Bind` mock to return `nil` for empty password (matching real unauthenticated-bind LDAP server semantics), demonstrating that `CreateSession` grants a session without enforcing a non-empty password check.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L406-411)
```go
	escapedEmail := ldap.EscapeFilter(strings.ToLower(sr.Email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	if err = conn.Bind(searchBaseDN, sr.Password); err != nil {
		l.lggr.Infof("Error binding user authentication request in LDAP Bind: %v", err)
		returnErr = errors.New("unable to log in with LDAP server. Check credentials")
	}
```

**File:** core/sessions/ldapauth/ldap.go (L413-456)
```go
	// Bind was successful meaning user and credentials are present in LDAP directory
	// Reuse FindUser functionality to fetch user roles used to create ldap_session entry
	// with cached user email and role
	foundUser, err := l.FindUser(ctx, escapedEmail)
	if err != nil {
		l.lggr.Infof("Successful user login, but error querying for user groups: user: %s, error %v", escapedEmail, err)
		returnErr = errors.New("log in successful, but no assigned groups to assume role")
	}

	isLocalUser := false
	if returnErr != nil {
		// Unable to log in against LDAP server, attempt fallback local auth with credentials, case of local CLI Admin account
		// Successful local user sessions can not be managed by the upstream server and have expiration handled by the reaper sync module
		foundUser, returnErr = l.localLoginFallback(ctx, sr)
		isLocalUser = true
	}

	// If err is still populated, return
	if returnErr != nil {
		return "", returnErr
	}

	l.lggr.Infof("Successful LDAP login request for user %s - %s", sr.Email, foundUser.Role)

	// Save session, user, and role to database. Given a session ID for future queries, the LDAP server will not be queried
	// Sessions are set to expire after the duration + creation date elapsed, and are synced on an interval against the upstream
	// LDAP server
	session := sessions.NewSession()
	_, err = l.ds.ExecContext(
		ctx,
		"INSERT INTO ldap_sessions (id, user_email, user_role, localauth_user, created_at) VALUES ($1, $2, $3, $4, now())",
		session.ID,
		strings.ToLower(sr.Email),
		foundUser.Role,
		isLocalUser,
	)
	if err != nil {
		l.lggr.Errorf("unable to create new session in ldap_sessions table %v", err)
		return "", fmt.Errorf("error creating local LDAP session: %w", err)
	}

	l.auditLogger.Audit(audit.AuthLoginSuccessNo2FA, map[string]any{"email": sr.Email})

	return session.ID, nil
```

**File:** core/sessions/ldapauth/ldap.go (L504-514)
```go
func (l *ldapAuthenticator) TestPassword(ctx context.Context, email string, password string) error {
	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		return errors.New("unable to establish connection to LDAP server with provided URL and credentials")
	}
	defer conn.Close()

	// Attempt to LDAP Bind with user provided credentials
	escapedEmail := ldap.EscapeFilter(strings.ToLower(email))
	searchBaseDN := fmt.Sprintf("%s=%s,%s,%s", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	err = conn.Bind(searchBaseDN, password)
```

**File:** core/sessions/ldapauth/client.go (L21-25)
```go
type LDAPConn interface {
	Search(searchRequest *ldap.SearchRequest) (*ldap.SearchResult, error)
	Bind(username string, password string) error
	Close() (err error)
}
```

**File:** core/web/sessions_controller.go (L34-56)
```go
	session := sessions.Default(c)
	var sr clsessions.SessionRequest
	if err := c.ShouldBindJSON(&sr); err != nil {
		jsonAPIError(c, http.StatusBadRequest, fmt.Errorf("error binding json %w", err))
		return
	}

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

	sid, err := sc.App.AuthenticationProvider().CreateSession(ctx, sr)
```

**File:** core/sessions/session.go (L16-22)
```go
type SessionRequest struct {
	Email          string `json:"email"`
	Password       string `json:"password"`
	WebAuthnData   string `json:"webauthndata"`
	WebAuthnConfig WebAuthnConfiguration
	SessionStore   *WebAuthnSessionStore
}
```
