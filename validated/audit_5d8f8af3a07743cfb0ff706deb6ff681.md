### Title
Demoted admin's LDAP-issued API token retains stale elevated role for full `UserAPITokenDuration` when upstream group re-sync errors or is rate-limited - ([File: core/sessions/ldapauth/sync.go])

### Summary
`LDAPServerStateSyncer.Work` purges API tokens only by age (`deleteStaleAPITokens`, using `UserAPITokenDuration`), completely independent from the role re-verification/downgrade logic that lives later in `Work()` behind the `UpstreamSyncRateLimit` gate and the four `ldapGroupMembersListToUser` calls. If any of those calls errors, or if the rate-limit window hasn't elapsed, `Work()` returns before reaching the transactional `UPDATE ldap_user_api_tokens SET user_role = ...` block, so a token issued while a user was Admin keeps its cached `user_role = admin` value in the `ldap_user_api_tokens` table. Because `FindUserByAPIToken` trusts this cached `user_role` column directly with no live LDAP re-check, a demoted user can continue making privileged API calls with their old token until it naturally expires after `UserAPITokenDuration`.

### Finding Description
Token-based auth reads the cached role straight from the DB without any live verification: `FindUserByAPIToken` executes `SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_user_api_tokens WHERE token_key = $1` and returns `sessions.User{Email: ..., Role: foundUserToken.UserRole}` as long as the token hasn't aged past `UserAPITokenDuration` [1](#0-0) . That `user_role` value is only ever updated by the CASE/WHEN `UPDATE ldap_user_api_tokens` statement inside the transaction at the end of `Work()` [2](#0-1) .

Reaching that transaction requires passing through all of: the `UpstreamSyncRateLimit` gate (`return` if not yet elapsed) [3](#0-2) , and four sequential `ldapGroupMembersListToUser` calls, each of which does an unconditional `return` on error before any downgrade logic runs [4](#0-3) .

Meanwhile, `deleteStaleAPITokens`, called unconditionally at the top of `Work()` regardless of rate-limit/error state, purges rows purely by `created_at < before` using `UserAPITokenDuration` — it has no notion of role correctness [5](#0-4) , and this call happens before the rate-limit/group-sync section entirely [6](#0-5) .

Root cause: privilege downgrade for cached API tokens is coupled to a best-effort, rate-limited, fail-open upstream sync step, while token lifetime/purge is governed by an unrelated age-based rule. Any transient LDAP query failure (network blip, group DN issue, timeout) or simply being inside the `UpstreamSyncRateLimit` window causes `Work()` to skip the downgrade step silently (only logged), violating the fail-closed invariant for privilege downgrade.

### Impact Explanation
A user who was granted an Admin-role API token via `SetAuthToken`/`CreateAndSetAuthToken` [7](#0-6)  and is later demoted or removed from the upstream Admin LDAP group continues to be treated as Admin by `AuthenticateByToken` for every request as long as the sync pipeline hasn't successfully completed a full role-refresh pass for that user. This grants prolonged unauthorized privileged API access (up to the full `UserAPITokenDuration`), matching a privilege-escalation/broken-access-control impact class.

### Likelihood Explanation
This requires no attacker action beyond already possessing a previously-legitimate API token (the demoted user itself, or anyone who obtained/retained that token). The trigger condition — an LDAP query error on any of the four group lookups, or simply operating within the configured `UpstreamSyncRateLimit` window — is a realistic and repeatable operational condition (LDAP server hiccups, timeouts, misconfiguration, or just normal rate-limit cadence), not a rare edge case, making this readily reproducible in test and plausible in production.

### Recommendation
Decouple/harden the downgrade path from the optimistic re-sync: on any error from `ldapGroupMembersListToUser` (or `validateUsersActive`), fail closed for privilege downgrade — e.g., mark affected/all cached tokens and sessions as requiring immediate re-verification (or revoke them) rather than silently returning and preserving prior elevated roles. Additionally, consider re-validating role/active-status live against LDAP (or against the most recent successful sync snapshot with a bounded staleness) inside `FindUserByAPIToken`/`AuthenticateByToken` rather than trusting an unboundedly stale cached `user_role`.

### Proof of Concept
Unit test in `core/sessions/ldapauth`:
1. Seed `ldap_user_api_tokens` with a row for `admin@test.com`, `user_role='admin'`, valid `token_key`, and `created_at = now()`.
2. Configure a mock `LDAPClient`/connection such that the query for `AdminUserGroupCN()` (first call to `ldapGroupMembersListToUser` inside `Work`) returns an error (simulating the admin having been removed from the group, or a transient LDAP failure).
3. Call `syncer.Work(ctx)`.
4. Assert `Work` returns without error being propagated further and without reaching the transactional UPDATE (e.g., via a spy/mock `sqlutil.DataSource` asserting `TransactDataSource` was never invoked, or by checking logs).
5. Query `ldap_user_api_tokens` and assert the row for `admin@test.com` still has `user_role='admin'` and was not deleted.
6. Call `ldapAuthProvider.FindUserByAPIToken(ctx, apiToken)` and assert it still returns `sessions.User{Role: sessions.UserRoleAdmin}`, proving the demoted user retains admin-level API access.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L204-236)
```go
// FindUserByAPIToken retrieves a possible stored user and role from the ldap_user_api_tokens table store
func (l *ldapAuthenticator) FindUserByAPIToken(ctx context.Context, apiToken string) (sessions.User, error) {
	if !l.config.UserApiTokenEnabled() {
		return sessions.User{}, errors.New("API token is not enabled ")
	}

	// Query the ldap user API token table for given token, user role and email are cached so
	// no further upstream LDAP query is performed, sessions and tokens are synced against the upstream server
	// via the UpstreamSyncInterval config and reaper.go sync implementation
	var foundUserToken struct {
		UserEmail string
		UserRole  sessions.UserRole
		Valid     bool
	}
	err := l.ds.GetContext(ctx, &foundUserToken,
		"SELECT user_email, user_role, created_at + $2 >= now() as valid FROM ldap_user_api_tokens WHERE token_key = $1",
		apiToken, l.config.UserAPITokenDuration().Duration(),
	)
	if err != nil {
		return sessions.User{}, err
	}
	if !foundUserToken.Valid { // API Token expired, purge
		if _, execErr := l.ds.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE token_key = $1", apiToken); execErr != nil {
			l.lggr.Errorf("error purging stale ldap API token session: %v", execErr)
		}
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	return sessions.User{
		Email: foundUserToken.UserEmail,
		Role:  foundUserToken.UserRole,
	}, nil
}
```

**File:** core/sessions/ldapauth/ldap.go (L532-592)
```go
// CreateAndSetAuthToken generates a new credential token with the user role
func (l *ldapAuthenticator) CreateAndSetAuthToken(ctx context.Context, user *sessions.User) (*auth.Token, error) {
	newToken := auth.NewToken()

	err := l.SetAuthToken(ctx, user, newToken)
	if err != nil {
		return nil, err
	}

	return newToken, nil
}

// SetAuthToken updates the user to use the given Authentication Token.
func (l *ldapAuthenticator) SetAuthToken(ctx context.Context, user *sessions.User, token *auth.Token) error {
	if !l.config.UserApiTokenEnabled() {
		return errors.New("API token is not enabled ")
	}

	salt := utils.NewSecret(utils.DefaultSecretSize)
	hashedSecret, err := auth.HashedSecret(token, salt)
	if err != nil {
		return fmt.Errorf("LDAPAuth SetAuthToken hashed secret error: %w", err)
	}

	err = sqlutil.TransactDataSource(ctx, l.ds, nil, func(tx sqlutil.DataSource) error {
		// Is this user a local CLI Admin or upstream LDAP user?
		// Check presence in local users table. Set localauth_user column true if present.
		// This flag omits the session/token from being purged by the sync daemon/reaper.go
		isLocalCLIAdmin := false
		err = l.ds.QueryRowxContext(ctx, "SELECT EXISTS (SELECT 1 FROM users WHERE email = $1)", user.Email).Scan(&isLocalCLIAdmin)
		if err != nil {
			return fmt.Errorf("error checking user presence in users table: %w", err)
		}

		// Remove any existing API tokens
		if _, err = l.ds.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE user_email = $1", user.Email); err != nil {
			return fmt.Errorf("error executing DELETE FROM ldap_user_api_tokens: %w", err)
		}
		// Create new API token for user
		_, err = l.ds.ExecContext(
			ctx,
			"INSERT INTO ldap_user_api_tokens (user_email, user_role, localauth_user, token_key, token_salt, token_hashed_secret, created_at) VALUES ($1, $2, $3, $4, $5, $6, now())",
			user.Email,
			user.Role,
			isLocalCLIAdmin,
			token.AccessKey,
			salt,
			hashedSecret,
		)
		if err != nil {
			return fmt.Errorf("failed insert into ldap_user_api_tokens: %w", err)
		}
		return nil
	})
	if err != nil {
		return errors.New("error creating API token")
	}

	l.auditLogger.Audit(audit.APITokenCreated, map[string]any{"user": user.Email})
	return nil
}
```

**File:** core/sessions/ldapauth/sync.go (L100-104)
```go
	recordCreationStaleThreshold = l.config.UserAPITokenDuration().Before(time.Now())
	err = l.deleteStaleAPITokens(ctx, recordCreationStaleThreshold)
	if err != nil {
		l.lggr.Error("unable to expire user API tokens: ", err)
	}
```

**File:** core/sessions/ldapauth/sync.go (L107-114)
```go
	if !l.config.UpstreamSyncRateLimit().IsInstant() {
		if !time.Now().After(l.nextSyncTime) {
			return
		}

		// Enough time has elapsed to sync again, store the time for when next sync is allowed and begin sync
		l.nextSyncTime = time.Now().Add(l.config.UpstreamSyncRateLimit().Duration())
	}
```

**File:** core/sessions/ldapauth/sync.go (L133-156)
```go
	// Query for list of uniqueMember IDs present in Admin group
	adminUsers, err := l.ldapGroupMembersListToUser(conn, l.config.AdminUserGroupCN(), sessions.UserRoleAdmin)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	editUsers, err := l.ldapGroupMembersListToUser(conn, l.config.EditUserGroupCN(), sessions.UserRoleEdit)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	runUsers, err := l.ldapGroupMembersListToUser(conn, l.config.RunUserGroupCN(), sessions.UserRoleRun)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
	// Query for list of uniqueMember IDs present in Edit group
	readUsers, err := l.ldapGroupMembersListToUser(conn, l.config.ReadUserGroupCN(), sessions.UserRoleView)
	if err != nil {
		l.lggr.Error("Error in ldapGroupMembersListToUser: ", err)
		return
	}
```

**File:** core/sessions/ldapauth/sync.go (L245-275)
```go
		// For each user session row, update role to match state of user map from upstream source
		var queryWhenClause strings.Builder
		emailValues := []any{}
		// Prepare CASE WHEN query statement with parameterized argument $n placeholders and matching role based on index
		for email, user := range upstreamUserStateMap {
			// Only build on SET CASE statement per local session and API token role, not for each upstream user value
			_, sessionOk := existingSessionsMap[email]
			_, tokenOk := existingAPITokensMap[email]
			if !sessionOk && !tokenOk {
				continue
			}
			emailValues = append(emailValues, email)
			fmt.Fprintf(&queryWhenClause, "WHEN user_email = $%d THEN '%s' ", len(emailValues), user.Role)
		}

		// If there are remaining user entries to update
		if len(emailValues) != 0 {
			// Set new role state for all rows in single Exec
			query := fmt.Sprintf("UPDATE ldap_sessions SET user_role = CASE %s ELSE user_role END", &queryWhenClause)
			_, err = tx.ExecContext(ctx, query, emailValues...)
			if err != nil {
				return err
			}

			// Update role of API tokens as well
			query = fmt.Sprintf("UPDATE ldap_user_api_tokens SET user_role = CASE %s ELSE user_role END", &queryWhenClause)
			_, err = tx.ExecContext(ctx, query, emailValues...)
			if err != nil {
				return err
			}
		}
```

**File:** core/sessions/ldapauth/sync.go (L292-296)
```go
// deleteStaleAPITokens deletes all ldap_user_api_tokens before the passed time.
func (l *LDAPServerStateSyncer) deleteStaleAPITokens(ctx context.Context, before time.Time) error {
	_, err := l.ds.ExecContext(ctx, "DELETE FROM ldap_user_api_tokens WHERE created_at < $1", before)
	return err
}
```
