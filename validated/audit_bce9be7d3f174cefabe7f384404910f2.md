### Title
Chainlink Node Web UI: Ineffective / Misaligned Session Timeout and Missing Absolute Session Lifetime — ([File: core/services/chainlink/config_web_server.go])

### Summary
The Chainlink node operator UI (web server) advertises a 15-minute idle `SessionTimeout` but this value is not what actually limits the persisted-cookie lifetime, and session validity is instead governed by a much longer, sliding window with no absolute cap.

### Finding Description
The gin session cookie used to authenticate the operator UI is configured with a hardcoded 30-day `MaxAge`, independent of the documented `WebServer.SessionTimeout` config value: [1](#0-0) 

This cookie store is wired into the router as the session backing store for all authenticated API routes: [2](#0-1) 

Actual per-request session validity is checked server-side in the local-auth ORM, which compares `last_used + sessionDuration >= now()`: [3](#0-2) 

Critically, every authorized request calls `updateSessionLastUsed`, which resets the `last_used` timestamp to `now()` on every single authenticated call: [4](#0-3) 

This creates a purely *sliding* session with no enforced maximum/absolute lifetime: as long as any authenticated request (including background polling from an open UI tab, e.g. `/ping`, GraphQL queries, `/config`, etc.) occurs at least once within the `sessionDuration` window, the session remains valid indefinitely. The `SessionReaperExpiration` config (`240h` / 10 days by default) governs when *stale, completely unused* sessions are purged from the DB by the background reaper: [5](#0-4) 

but this reaper only removes sessions that have been idle for the full `SessionTimeout + SessionReaperExpiration` window — it does not enforce a maximum session lifetime for actively (even passively/automatically) used sessions.

The documented `SessionTimeout` ("determines the amount of idle time to elapse before session cookies expire... signs out GUI users from their sessions", default `15m`) is therefore misleading: the actual browser cookie persists for 30 days regardless of this setting, and combined with a self-extending server-side session, a user (or an attacker holding a stolen cookie/token) can remain authenticated far beyond the documented idle window and with no absolute session expiry, closely matching the reported bug class of "no idle timeout / extended auth lifetime."

### Impact Explanation
An attacker who obtains a valid session cookie (e.g., via a compromised endpoint, shared/unattended browser, or stolen browser storage) can retain full authenticated access to the Chainlink node's operator UI/API — including privileged actions gated by `RequiresAdminRole`/`RequiresEditRole`/`RequiresRunRole` such as key management, job creation/deletion, ETH key export, and transaction/transfer endpoints — for an extended, effectively unbounded period, since the session keeps sliding forward as long as it is used at all, and the underlying cookie is valid for 30 days regardless of configuration. [6](#0-5) 

### Likelihood Explanation
Any user who leaves an operator UI tab open (which regularly issues background/polling requests) or whose session cookie/token is exfiltrated will benefit from this sliding-window behavior without needing to actively interact with the UI. No special privileges are required to trigger the condition — it is inherent to how `AuthorizedUserWithSession` refreshes `last_used` on every call.

### Recommendation
- Enforce the documented `SessionTimeout` as the actual cookie `MaxAge` (or as a server-side sliding check) instead of a hardcoded `86400*30` in `SessionOptions()`.
- Introduce a true absolute/maximum session lifetime (independent of activity) tracked from session creation time, and reject sessions past that absolute limit even if `last_used` is recent.
- Ensure logout (`SessionsController.Destroy`) invalidation is paired with a shortened, config-driven cookie `MaxAge` so that stolen cookies cannot be replayed long after the advertised idle timeout.

### Proof of Concept
1. Log in to the Chainlink node operator UI, obtaining a `clsession` cookie (30-day `MaxAge` per `SessionOptions()`).
2. Leave the UI tab open (or replay the stolen cookie) making periodic authenticated requests (e.g., polling `/v2/ping` or GraphQL) at intervals shorter than `SessionReaperExpiration` (10 days by default).
3. Observe that despite `WebServer.SessionTimeout = 15m` being configured, the session never expires because `updateSessionLastUsed` resets the idle clock on each request, and the cookie itself remains valid for 30 days — demonstrating no enforced idle timeout and no absolute session lifetime.

### Citations

**File:** core/services/chainlink/config_web_server.go (L168-175)
```go
func (w *webServerConfig) SessionOptions() sessions.Options {
	return sessions.Options{
		Secure:   w.SecureCookies(),
		HttpOnly: true,
		MaxAge:   86400 * 30,
		SameSite: http.SameSiteStrictMode,
	}
}
```

**File:** core/web/router.go (L52-57)
```go
	secret, err := app.SecretGenerator().Generate(config.RootDir())
	if err != nil {
		return nil, err
	}
	sessionStore := cookie.NewStore(secret)
	sessionStore.Options(config.WebServer().SessionOptions())
```

**File:** core/web/router.go (L238-256)
```go
func v2Routes(app chainlink.Application, r *gin.RouterGroup) {
	unauthedv2 := r.Group("/v2")

	prc := PipelineRunsController{app}
	psec := PipelineJobSpecErrorsController{app}
	unauthedv2.PATCH("/resume/:runID", prc.Resume)

	authv2 := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	{
		uc := UserController{app}
		authv2.GET("/users", auth.RequiresAdminRole(uc.Index))
		authv2.POST("/users", auth.RequiresAdminRole(uc.Create))
		authv2.PATCH("/users", auth.RequiresAdminRole(uc.UpdateRole))
		authv2.DELETE("/users/:email", auth.RequiresAdminRole(uc.Delete))
		authv2.PATCH("/user/password", uc.UpdatePassword)
		authv2.POST("/user/token", uc.NewAPIToken)
```

**File:** core/sessions/localauth/orm.go (L68-75)
```go
// findValidSession finds an unexpired session by its ID and returns the associated email.
func (o *orm) findValidSession(ctx context.Context, sessionID string) (email string, err error) {
	if err := o.ds.GetContext(ctx, &email, "SELECT email FROM sessions WHERE id = $1 AND last_used + $2 >= now() FOR UPDATE", sessionID, o.sessionDuration); err != nil {
		o.lggr.Infof("query result: %v", email)
		return email, pkgerrors.Wrap(err, "no matching user for provided session token")
	}
	return email, nil
}
```

**File:** core/sessions/localauth/orm.go (L77-107)
```go
// updateSessionLastUsed updates a session by its ID and sets the LastUsed field to now().
func (o *orm) updateSessionLastUsed(ctx context.Context, sessionID string) error {
	_, err := o.ds.ExecContext(ctx, "UPDATE sessions SET last_used = now() WHERE id = $1", sessionID)
	return err
}

// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired, and update session's LastUsed field.
// AuthorizedUserWithSession will return the API user associated with the Session ID if it
// exists and hasn't expired, and update session's LastUsed field.
func (o *orm) AuthorizedUserWithSession(ctx context.Context, sessionID string) (user sessions.User, err error) {
	if len(sessionID) == 0 {
		return sessions.User{}, sessions.ErrEmptySessionID
	}

	email, err := o.findValidSession(ctx, sessionID)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	user, err = o.findUser(ctx, email)
	if err != nil {
		return sessions.User{}, sessions.ErrUserSessionExpired
	}

	if err := o.updateSessionLastUsed(ctx, sessionID); err != nil {
		return sessions.User{}, err
	}

	return user, nil
}
```

**File:** core/sessions/localauth/reaper.go (L35-48)
```go
func (sr *sessionReaper) Work(ctx context.Context) {
	recordCreationStaleThreshold := sr.config.SessionReaperExpiration().Before(
		sr.config.SessionTimeout().Before(time.Now()))
	err := sr.deleteStaleSessions(ctx, recordCreationStaleThreshold)
	if err != nil {
		sr.lggr.Error("unable to reap stale sessions: ", err)
	}
}

// DeleteStaleSessions deletes all sessions before the passed time.
func (sr *sessionReaper) deleteStaleSessions(ctx context.Context, before time.Time) error {
	_, err := sr.ds.ExecContext(ctx, "DELETE FROM sessions WHERE last_used < $1", before)
	return err
}
```
