### Title
LDAP `ActiveAttribute` deactivation check is not enforced in the `CreateSession` login path - ([File: core/sessions/ldapauth/ldap.go])

### Summary
The LDAP authenticator's `validateUsersActive` (which checks the configured `ActiveAttribute` against the upstream LDAP directory) is only invoked from `ListUsers` and the background sync/reaper logic in `sync.go`, but not from the user-facing authentication path (`FindUser`, which resolves the user's role during `CreateSession`). This means a deactivated LDAP account can still bind successfully and obtain a valid session before the next background sync runs.

### Finding Description
`FindUser` in `core/sessions/ldapauth/ldap.go` performs the group-membership lookup used to authenticate/authorize a user, but it never calls `validateUsersActive`: [1](#0-0) 

`validateUsersActive`, the function that performs the extra LDAP query for the `ActiveAttribute`/`ActiveAttributeAllowedValue` and returns whether each user is currently active, is defined here: [2](#0-1) 

The only caller of `validateUsersActive` inside `ldap.go` is `ListUsers` (used for admin user listing, not the login/session-creation flow): [3](#0-2) 

The remaining callers are in `sync.go`, which implements the periodic background reaper/revalidation driven by `UpstreamSyncInterval`, not the synchronous request path taken by `CreateSession`.

The package-level doc comment states that "user session and roles are cached and revalidated with the upstream service at the interval defined in the local LDAP config through the Application.sessionReaper implementation," confirming that active-status revalidation is designed to be interval-based rather than enforced at every login: [4](#0-3) 

Because `FindUser`/the credential-bind flow used by `CreateSession` never calls `validateUsersActive`, deactivating a user's `ActiveAttribute` upstream has no immediate effect on their ability to authenticate — only on `ListUsers` output and on the periodic reaper's local table cleanup. If `UpstreamSyncInterval` is set to `0s` (disabling the background reaper), the deactivation is never enforced against session creation at all for credentials that still validate against the LDAP bind (or against the local fallback `users` table/`ldap_sessions` cache).

### Impact Explanation
A deactivated/revoked LDAP account whose credentials are still technically valid (e.g., password bind still works, or a stale local record exists) can continue to call `CreateSession` and obtain a new, valid, privileged session, bypassing the intended access-revocation control. This is unauthorized session issuance for a deactivated account — continued privileged access after access should have been revoked.

### Likelihood Explanation
Requires `AuthenticationMethod=ldap` with `ActiveAttribute` configured, which is an explicit opt-in security control meant to enforce deactivation. The precondition of `UpstreamSyncInterval='0s'` maximizes the exposure window (no periodic re-check at all), but even with a non-zero interval there remains a window between deactivation and the next reaper cycle during which `CreateSession` succeeds, because the check is structurally absent from the login path, not merely delayed.

### Recommendation
Invoke `validateUsersActive` (or an equivalent single-user active check) for the authenticating user's email inside `FindUser`/the `CreateSession` code path before returning a valid `sessions.User`, so that deactivation is enforced synchronously at login time rather than relying solely on the interval-based reaper in `sync.go`.

### Proof of Concept
Integration test plan:
1. Configure a mock LDAP server/client with `ActiveAttribute` set, and a test user that initially passes both group-membership and active checks.
2. Call `CreateSession` with valid credentials — assert session is created successfully.
3. Update the mock LDAP responder so the same user's `ActiveAttribute` no longer matches `ActiveAttributeAllowedValue` (simulating deactivation), without triggering the background reaper (`UpstreamSyncInterval` set to `0s` or reaper not yet run).
4. Immediately call `CreateSession` again with the same still-valid credentials.
5. Expected (secure) behavior: session creation fails due to inactive account.
6. Actual behavior per code review: `FindUser` returns the user/role without consulting `validateUsersActive`, so `CreateSession` succeeds and issues a valid session ID — confirming the vulnerability.

### Citations

**File:** core/sessions/ldapauth/ldap.go (L1-23)
```go
/*
The LDAP authentication package forwards the credentials in the user session request
for authentication with a configured upstream LDAP server

This package relies on the two following local database tables:

	ldap_sessions: 	Upon successful LDAP response, creates a keyed local copy of the user email
	ldap_user_api_tokens: User created API tokens, tied to the node, storing user email.

Note: user can have only one API token at a time, and token expiration is enforced

User session and roles are cached and revalidated with the upstream service at the interval defined in
the local LDAP config through the Application.sessionReaper implementation in reaper.go.

Changes to the upstream identity server will propagate through and update local tables (web sessions, API tokens)
by either removing the entries or updating the roles. This sync happens for every auth endpoint hit, and
via the defined sync interval. One goroutine is created to coordinate the sync timing in the New function

This implementation is read only; user mutation actions such as Delete are not supported.

MFA is supported via the remote LDAP server implementation. Sufficient request time out should accommodate
for a blocking auth call while the user responds to a potential push notification callback.
*/
```

**File:** core/sessions/ldapauth/ldap.go (L114-202)
```go
// FindUser will attempt to return an LDAP user with mapped role by email.
func (l *ldapAuthenticator) FindUser(ctx context.Context, email string) (sessions.User, error) {
	email = strings.ToLower(email)

	// First check for the supported local admin users table
	var foundLocalAdminUser sessions.User
	checkErr := l.ds.GetContext(ctx, &foundLocalAdminUser, "SELECT * FROM users WHERE lower(email) = lower($1)", email)
	if checkErr == nil {
		return foundLocalAdminUser, nil
	}
	// If error is not nil, there was either an issue or no local users found
	if !errors.Is(checkErr, sql.ErrNoRows) {
		// If the error is not that no local user was found, log and exit
		l.lggr.Errorf("error searching users table: %v", checkErr)
		return sessions.User{}, errors.New("error Finding user")
	}

	// First query for user "is active" property if defined
	usersActive, err := l.validateUsersActive([]string{email})
	if err != nil {
		if errors.Is(err, ErrUserNotInUpstream) {
			return sessions.User{}, ErrUserNotInUpstream
		}
		l.lggr.Errorf("error in validateUsers call: %v", err)
		return sessions.User{}, errors.New("error running query to validate user active")
	}
	if !usersActive[0] {
		return sessions.User{}, errors.New("user not active")
	}

	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		l.lggr.Errorf("error in LDAP dial: %v", err)
		return sessions.User{}, errors.New("unable to establish connection to LDAP server with provided URL and credentials")
	}
	defer conn.Close()

	// User email and role are the only upstream data that needs queried for.
	// List query user groups using the provided email, on success is a list of group the uniquemember belongs to
	// data is readily available
	escapedEmail := ldap.EscapeFilter(email)
	searchBaseDN := fmt.Sprintf("%s, %s", l.config.GroupsDN(), l.config.BaseDN())
	filterQuery := fmt.Sprintf("(&(uniquemember=%s=%s,%s,%s))", l.config.BaseUserAttr(), escapedEmail, l.config.UsersDN(), l.config.BaseDN())
	searchRequest := ldap.NewSearchRequest(
		searchBaseDN,
		ldap.ScopeWholeSubtree, ldap.NeverDerefAliases,
		0, int(l.config.QueryTimeout().Seconds()), false,
		filterQuery,
		[]string{"cn"},
		nil,
	)

	// Query the server
	result, err := conn.Search(searchRequest)
	if err != nil {
		l.lggr.Errorf("error searching users in LDAP query: %v", err)
		return sessions.User{}, errors.New("error searching users in LDAP directory")
	}

	if len(result.Entries) == 0 {
		// Provided email is not present in upstream LDAP server, local admin CLI auth is supported
		// So query and check the users table as well before failing
		var localUserRole sessions.UserRole
		if err = l.ds.GetContext(ctx, &localUserRole, "SELECT role FROM users WHERE email = $1", email); err != nil {
			// Above query for local user unsuccessful, return error
			l.lggr.Warnf("No local users table user found with email %s", email)
			return sessions.User{}, errors.New("no users found with provided email")
		}

		// If the above query to the local users table was successful, return that local user's role
		return sessions.User{
			Email: email,
			Role:  localUserRole,
		}, nil
	}

	// Populate found user by email and role based on matched group names
	userRole, err := l.groupSearchResultsToUserRole(result.Entries)
	if err != nil {
		l.lggr.Warnf("User '%s' found but no matching assigned groups in LDAP to assume role", email)
		return sessions.User{}, err
	}

	// Convert search result to sessions.User type with required fields
	return sessions.User{
		Email: email,
		Role:  userRole,
	}, nil
}
```

**File:** core/sessions/ldapauth/ldap.go (L292-315)
```go
	// If no active attribute to check is defined, user simple being assigned the group is enough, return full list
	if l.config.ActiveAttribute() == "" {
		return dedupedUsers, nil
	}

	// Now optionally validate that all uniqueMembers are active in the org/LDAP server
	emails := []string{}
	for _, user := range dedupedUsers {
		emails = append(emails, user.Email)
	}
	activeUsers, err := l.validateUsersActive(emails)
	if err != nil {
		l.lggr.Error("error validating supplied user list: ", err)
		return users, errors.New("error validating supplied user list")
	}

	// Filter non active users
	returnUsers := []sessions.User{}
	for i, active := range activeUsers {
		if active {
			returnUsers = append(returnUsers, dedupedUsers[i])
		}
	}

```

**File:** core/sessions/ldapauth/ldap.go (L644-709)
```go
// validateUsersActive performs an additional LDAP server query for the supplied emails, checking the
// returned user data for an 'active' property defined optionally in the config.
// Returns same length bool 'valid' array, indexed by sorted email
func (l *ldapAuthenticator) validateUsersActive(emails []string) ([]bool, error) {
	validUsers := make([]bool, len(emails))
	// If active attribute to check is not defined in config, skip
	if l.config.ActiveAttribute() == "" {
		// fill with valids
		for i := range emails {
			validUsers[i] = true
		}
		return validUsers, nil
	}

	conn, err := l.ldapClient.CreateEphemeralConnection()
	if err != nil {
		l.lggr.Error("error in LDAP dial: ", err)
		return validUsers, errors.New("unable to establish connection to LDAP server with provided URL and credentials")
	}
	defer conn.Close()

	// Build the full email list query to pull all 'isActive' information for each user specified in one query
	filterQuery := "(|"
	for _, email := range emails {
		escapedEmail := ldap.EscapeFilter(email)
		filterQuery = fmt.Sprintf("%s(%s=%s)", filterQuery, l.config.BaseUserAttr(), escapedEmail)
	}
	filterQuery = fmt.Sprintf("(&%s))", filterQuery)
	searchBaseDN := fmt.Sprintf("%s,%s", l.config.UsersDN(), l.config.BaseDN())
	searchRequest := ldap.NewSearchRequest(
		searchBaseDN,
		ldap.ScopeWholeSubtree, ldap.NeverDerefAliases,
		0, int(l.config.QueryTimeout().Seconds()), false,
		filterQuery,
		[]string{l.config.BaseUserAttr(), l.config.ActiveAttribute()},
		nil,
	)
	// Query LDAP server for the ActiveAttribute property of each specified user
	results, err := conn.Search(searchRequest)
	if err != nil {
		l.lggr.Errorf("error searching user in LDAP query: %v", err)
		return validUsers, errors.New("error searching users in LDAP directory")
	}

	// Ensure user response entries
	if len(results.Entries) == 0 {
		return validUsers, ErrUserNotInUpstream
	}

	// Pull expected ActiveAttribute value from list of string possible values
	// keyed on email for final step to return flag bool list where order is preserved
	emailToActiveMap := make(map[string]bool)
	for _, result := range results.Entries {
		isActiveAttribute := result.GetAttributeValue(l.config.ActiveAttribute())
		uidAttribute := result.GetAttributeValue(l.config.BaseUserAttr())
		emailToActiveMap[uidAttribute] = isActiveAttribute == l.config.ActiveAttributeAllowedValue()
	}
	for i, email := range emails {
		active, ok := emailToActiveMap[email]
		if ok && active {
			validUsers[i] = true
		}
	}

	return validUsers, nil
}
```
