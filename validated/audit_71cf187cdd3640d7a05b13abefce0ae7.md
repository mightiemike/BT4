### Title
Non-`sql.ErrNoRows` errors from `FindUserByAPIToken`/`AuthorizedUserWithSession` leak raw internal error strings to unauthenticated callers - ([File: core/web/auth/auth.go], [File: core/web/auth/helpers.go])

### Summary
`AuthenticateByToken` and `AuthenticateBySession` only normalize `sql.ErrNoRows`/`clsessions.ErrUserSessionExpired` to the generic `auth.ErrorAuthFailed`; any other error returned by the datastore (e.g. driver/connection errors, context cancellation, wrapped ORM errors) is returned verbatim. `Authenticate` then passes that raw error straight to `jsonAPIError`, which serializes `err.Error()` into the HTTP response body without redaction.

### Finding Description
- `AuthenticateByToken` calls `authr.FindUserByAPIToken(ctx, token.AccessKey)`. The concrete implementation, `core/sessions/localauth/orm.go` `FindUserByAPIToken`, does `err = o.ds.GetContext(ctx, &user, sql, apiToken); return` [1](#0-0)  — this returns whatever error the underlying SQL driver/sqlx layer produces (e.g. connection failures, context deadline exceeded, driver-specific errors), not just `sql.ErrNoRows`.
- Back in `AuthenticateByToken`, only `sql.ErrNoRows` and `clsessions.ErrUserSessionExpired` are mapped to the safe sentinel `auth.ErrorAuthFailed`; any other error takes the `return err` branch, propagating the raw error unmodified [2](#0-1) .
- `AuthenticateBySession` has the same pattern: `AuthorizedUserWithSession` errors are returned directly with `return err` [3](#0-2) . Although the concrete `orm.AuthorizedUserWithSession` implementation currently maps most failures to `sessions.ErrUserSessionExpired`, the final `updateSessionLastUsed` error path still returns the raw DB error unmodified [4](#0-3) .
- `Authenticate` middleware loops through auth methods, breaking on the first error that isn't `auth.ErrorAuthFailed`, then calls `jsonAPIError(c, http.StatusUnauthorized, err)` with that raw error [5](#0-4) .
- `jsonAPIError` checks only whether `err` is already a `*models.JSONAPIErrors`; for any other error type it calls `c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))`, embedding the raw `err.Error()` string directly in the JSON response body sent to the client [6](#0-5) .

An unprivileged attacker only needs to send any request carrying `X-API-KEY`/`X-API-SECRET` headers to an endpoint wrapped with `auth.Authenticate`. If the backing datastore returns any error other than "no rows" (transient DB outage, connection pool exhaustion, statement timeout, context cancellation due to client disconnect, etc.), that error string — potentially containing internal detail such as SQL driver messages, connection info, or Go internal error wrapping context — is reflected back verbatim in the 401 response body.

### Impact Explanation
This is an information-disclosure issue (SECRET_ISOLATION invariant violation): internal error text intended for logs is exposed to any unauthenticated network caller. Depending on the underlying driver/error, this could reveal DB connectivity details, internal package/function names via wrapped errors (`pkg/errors.Wrap` call sites elsewhere in the codebase append contextual strings like "no matching user for provided session token"), or operational state (e.g., DB is down, timing out) useful for reconnaissance/DoS targeting. It does not by itself grant auth bypass, privilege escalation, or code execution — the scoped impact is limited to internal error-string disclosure.

### Likelihood Explanation
Exploitability depends on the datastore returning a non-`ErrNoRows` error during the auth check, which is not attacker-controlled directly (it requires a genuine transient failure, e.g., DB connection issue, timeout, or resource exhaustion) rather than being triggerable purely by header manipulation. This lowers likelihood compared to a fully attacker-triggerable bug, but such conditions (DB restarts, connection pool exhaustion under load, network blips, client-triggered context cancellation) are realistic in production and the code path is reachable by any unauthenticated request with no rate limiting or sanitization in `jsonAPIError`.

### Recommendation
In `AuthenticateByToken` and `AuthenticateBySession`, map every non-nil error from the store (not just `sql.ErrNoRows`/`ErrUserSessionExpired`) to `auth.ErrorAuthFailed` before returning, and log the underlying error server-side via `c.Error()`/logger instead of surfacing it. Alternatively/additionally, harden `jsonAPIError` to only ever emit a generic message (e.g. "Unauthorized") for non-`*models.JSONAPIErrors` errors reached through the `auth.Authenticate` path, while still recording the full error via `c.Error(err)` for internal logging/observability.

### Proof of Concept
Integration test in `core/web/auth`:
1. Construct a `gin.Context`/router wrapped with `auth.Authenticate(mockAuthr, auth.AuthenticateByToken)`.
2. Configure the `Authenticator` mock's `FindUserByAPIToken` to return `(sessions.User{}, errors.New("dial tcp 10.0.0.5:5432: connect: connection refused"))` — a non-`sql.ErrNoRows` error containing a fake internal detail string.
3. Send a request with `X-API-KEY`/`X-API-SECRET` headers set to any values.
4. Assert response status is `401` and assert the response JSON body's `errors[0].detail` field **does not** contain the injected string ("10.0.0.5" / "connection refused"); currently this assertion fails because `jsonAPIError` echoes `err.Error()` verbatim, confirming the leak.

### Citations

**File:** core/sessions/localauth/orm.go (L49-53)
```go
func (o *orm) FindUserByAPIToken(ctx context.Context, apiToken string) (user sessions.User, err error) {
	sql := "SELECT * FROM users WHERE token_key = $1"
	err = o.ds.GetContext(ctx, &user, sql, apiToken)
	return
}
```

**File:** core/sessions/localauth/orm.go (L102-104)
```go
	if err := o.updateSessionLastUsed(ctx, sessionID); err != nil {
		return sessions.User{}, err
	}
```

**File:** core/web/auth/auth.go (L63-66)
```go
	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}
```

**File:** core/web/auth/auth.go (L93-99)
```go
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}
```

**File:** core/web/auth/auth.go (L157-174)
```go
func Authenticate(store Authenticator, methods ...authMethod) gin.HandlerFunc {
	return func(c *gin.Context) {
		var err error
		for _, method := range methods {
			err = method(c, store)
			if !errors.Is(err, auth.ErrorAuthFailed) {
				break
			}
		}
		if err != nil {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, err)

			return
		}

		c.Next()
	}
```

**File:** core/web/auth/helpers.go (L15-23)
```go
func jsonAPIError(c *gin.Context, statusCode int, err error) {
	_ = c.Error(err).SetType(gin.ErrorTypePublic)
	var jsonErr *models.JSONAPIErrors
	if errors.As(err, &jsonErr) {
		c.JSON(statusCode, jsonErr)
		return
	}
	c.JSON(statusCode, models.NewJSONAPIErrorsWith(err.Error()))
}
```
