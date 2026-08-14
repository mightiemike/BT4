### Title
External Initiator credential bypasses the intended "EIs not allowed" restriction and per-job scope binding on `POST /v2/jobs/:ID/runs` - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` authenticates an External Initiator (EI) purely via access-key/secret comparison and then writes a synthetic `clsessions.User{Role: UserRoleRun}` into the same `SessionUserKey` slot used for real, password-authenticated users. `PipelineRunsController.Create` relies on `auth.GetAuthenticatedUser(c)` to decide "only users are allowed to run jobs using int IDs - EIs not allowed", but that check cannot distinguish a real user from an EI-elevated pseudo-user, so any valid EI credential can trigger job runs for arbitrary numeric job IDs, not just jobs it was provisioned for.

### Finding Description
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` (lines 119-151) looks up the EI by access key, verifies the secret via `bridges.AuthenticateExternalInitiator` (constant-time hash compare, `core/bridges/external_initiator.go` lines 61-67), and on success does: [1](#0-0) 
This sets `SessionUserKey` to the exact same context key used by `AuthenticateBySession`/`AuthenticateByToken` for real users [2](#0-1) [3](#0-2) .

The route `POST /v2/jobs/:ID/runs` is wrapped by `RequiresRunRole` and mounted on a group that accepts `AuthenticateExternalInitiator`, `AuthenticateByToken`, or `AuthenticateBySession`: [4](#0-3) 

Inside the handler, the code attempts to explicitly block EIs from the int-ID run path via a comment and an `isUser` check: [5](#0-4) 
However, `isUser` is derived from `auth.GetAuthenticatedUser(c)`, which only checks presence of `SessionUserKey` — it cannot tell a genuine authenticated `User` apart from the synthetic `UserRoleRun` object set by `AuthenticateExternalInitiator`. Since that object is also stored under `SessionUserKey`, `isUser` is `true` for a successfully authenticated External Initiator, defeating the intended "EIs not allowed" restriction. Additionally, there is no check anywhere in this path binding the specific job ID being run to the specific EI identity in `SessionExternalInitiatorKey` — any EI can pass any numeric job ID and trigger `prc.App.RunJobV2`.

### Impact Explanation
Any party holding a legitimately-issued External Initiator credential (issued for one specific integration/job) can trigger `RunJobV2` for arbitrary job IDs on the node, not limited to jobs it was provisioned to trigger. Because job runs in Chainlink pipelines can include ETH/EVM transaction tasks, this allows an EI-scoped credential holder to cause unauthorized job execution and downstream transaction submission for jobs outside its intended scope, which matches "unauthorized job execution enabling unsafe transaction submission" impact.

### Likelihood Explanation
Exploitability requires possession of a valid, already-provisioned EI access key/secret (created by an operator with Edit role via `POST /external_initiators`, gated by `auth.RequiresEditRole` at [6](#0-5) ). Given that precondition — which is a normal operational grant, not a leaked/admin credential — the exploit is a single unauthenticated-role HTTP POST and is trivially repeatable; the only mitigation in place is the generic per-IP request-rate limiter (`rl.AuthenticatedPeriod()/Authenticated()`) applied to the whole `api` group [7](#0-6) , which is not scoped per-EI identity or per-job.

### Recommendation
Do not overload `SessionUserKey` for External Initiators — use a distinct context key/type (e.g., only `SessionExternalInitiatorKey`) so handlers can reliably distinguish EI-originated pseudo-role from real authenticated users. Fix `PipelineRunsController.Create` to check `auth.GetAuthenticatedExternalInitiator(c)` explicitly and reject the int-ID run path for EIs as the comment intends, or, if EI-triggered runs by ID are desired, bind each External Initiator record to the specific job(s)/bridge it is authorized to trigger and verify that binding before calling `RunJobV2`.

### Proof of Concept
Integration test plan (Go, using `core/web` test harness similar to `pipeline_runs_controller_test.go`):
1. Create two jobs, `jobA` (owned/intended for `EI1`) and `jobB` (unrelated to `EI1`).
2. Create `EI1` via the authenticated `/v2/external_initiators` endpoint using an Edit-role user session.
3. Using only `EI1`'s access key/secret headers (`X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret`), send `POST /v2/jobs/{jobB.ID}/runs`.
4. Assert: current behavior returns `200 OK` and a created pipeline run for `jobB`, proving `EI1` triggered a job outside its scope — this should instead return `401/403`.
5. Repeat with high frequency from the same EI credential across multiple unrelated job IDs and assert no additional per-EI/per-job rate limiting or scope binding exists beyond the generic IP rate limiter.

### Citations

**File:** core/web/auth/auth.go (L55-71)
```go
func AuthenticateBySession(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	session := sessions.Default(c)
	sessionID, ok := session.Get(SessionIDKey).(string)
	if !ok {
		return auth.ErrorAuthFailed
	}

	user, err := authr.AuthorizedUserWithSession(ctx, sessionID)
	if err != nil {
		return err
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/web/auth/auth.go (L78-112)
```go
func AuthenticateByToken(c *gin.Context, authr Authenticator) error {
	ctx := c.Request.Context()
	token := &auth.Token{
		AccessKey: c.GetHeader(APIKey),
		Secret:    c.GetHeader(APISecret),
	}
	if token.AccessKey == "" {
		return auth.ErrorAuthFailed
	}

	if token.Secret == "" {
		return auth.ErrorAuthFailed
	}

	// We need to first load the user row so we can compare tokens using the stored salt
	user, err := authr.FindUserByAPIToken(ctx, token.AccessKey)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) || errors.Is(err, clsessions.ErrUserSessionExpired) {
			return auth.ErrorAuthFailed
		}
		return err
	}

	ok, err := clsessions.AuthenticateUserByToken(token, &user)
	if err != nil {
		return err
	}
	if !ok {
		return auth.ErrorAuthFailed
	}

	c.Set(SessionUserKey, &user)

	return nil
}
```

**File:** core/web/auth/auth.go (L145-150)
```go
	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
```

**File:** core/web/router.go (L77-85)
```go
	rl := config.WebServer().RateLimit()
	api := engine.Group(
		"/",
		rateLimiter(
			rl.AuthenticatedPeriod(),
			rl.Authenticated(),
		),
		sessions.Sessions(auth.SessionName, sessionStore),
	)
```

**File:** core/web/router.go (L263-266)
```go
		eia := ExternalInitiatorsController{app}
		authv2.GET("/external_initiators", paginatedRequest(eia.Index))
		authv2.POST("/external_initiators", auth.RequiresEditRole(eia.Create))
		authv2.DELETE("/external_initiators/:Name", auth.RequiresEditRole(eia.Destroy))
```

**File:** core/web/router.go (L449-457)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
}
```

**File:** core/web/pipeline_runs_controller.go (L109-125)
```go
	_, isUser := auth.GetAuthenticatedUser(c)
	// only users are allowed to run jobs using int IDs - EIs not allowed
	if isUser {
		// Is it an int32? Then process it regardless of type
		var jobID int32
		jobID64, err := strconv.ParseInt(idStr, 10, 32)
		if err == nil {
			jobID = int32(jobID64)
			jobRunID, err := prc.App.RunJobV2(ctx, jobID, nil)
			if err != nil {
				jsonAPIError(c, http.StatusInternalServerError, err)
				return
			}
			respondWithPipelineRun(jobRunID)
			return
		}
	}
```
