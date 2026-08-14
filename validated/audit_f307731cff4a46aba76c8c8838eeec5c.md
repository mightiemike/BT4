### Title
External Initiator credentials bypass the "EIs not allowed" restriction and can trigger arbitrary job runs by int ID - ([File: core/web/pipeline_runs_controller.go])

### Summary
`PipelineRunsController.Create` intends to restrict int-ID job runs ("`/v2/jobs/:ID/runs`") to real user sessions/tokens only, explicitly excluding External Initiator (EI) credentials, per the code comment "only users are allowed to run jobs using int IDs - EIs not allowed". However, the check it uses, `auth.GetAuthenticatedUser(c)`, also returns `true` for EI-authenticated requests, because `auth.AuthenticateExternalInitiator` injects a synthetic `clsessions.User{Role: UserRoleRun}` into the same `SessionUserKey` context slot used by real user auth. This makes any successfully authenticated EI credential indistinguishable from a real user for this check, allowing it to trigger `RunJobV2` on any arbitrary job ID.

### Finding Description
The route is registered on the `userOrEI` group with `auth.RequiresRunRole(prc.Create)`, chaining `AuthenticateExternalInitiator`, `AuthenticateByToken`, `AuthenticateBySession`: [1](#0-0) 

`auth.AuthenticateExternalInitiator` sets `SessionUserKey` to a fabricated `clsessions.User` with `Role: UserRoleRun` whenever a valid EI access-key/secret pair is presented, in addition to `SessionExternalInitiatorKey`: [2](#0-1) 

`auth.RequiresRunRole` only checks `user.Role != UserRoleView`, so the synthetic EI user passes it, since its role is `UserRoleRun`: [3](#0-2) 

Inside `PipelineRunsController.Create`, the intended EI restriction is implemented solely via `auth.GetAuthenticatedUser(c)`, which merely reads back whatever object is stored at `SessionUserKey` — indistinguishable between a real authenticated user and the synthetic EI-injected user: [4](#0-3) [5](#0-4) 

Because `isUser` evaluates `true` for EI-authenticated requests, the handler proceeds to parse `idStr` as an arbitrary int32 job ID and calls `prc.App.RunJobV2(ctx, jobID, nil)` unconditionally — there is no lookup of `GetAuthenticatedExternalInitiator(c)` and no comparison between the EI's provisioned job/webhook and the requested `jobID` anywhere in this path (confirmed: `GetAuthenticatedExternalInitiator` is referenced only inside `core/web/auth/auth.go` and nowhere in `pipeline_runs_controller.go` or `application.go`). `RunJobV2` itself performs no EI-binding check either — it just loads the job by ID and executes its pipeline spec: [6](#0-5) 

The webhook-specific UUID binding mechanism that historically tied an EI to one particular job by external job UUID has been removed (webhook job type is rejected outright with `job.ErrJobTypeRemoved` before the `isUser` branch is even reached): [7](#0-6) 
This leaves the int-ID path as the only viable route for EI-triggered runs, and that path has zero EI-to-job binding.

### Impact Explanation
Any attacker holding one legitimate, low-privilege EI credential (issued for job A) can authenticate via `AuthenticateExternalInitiator` and then send `POST /v2/jobs/<jobB_ID>/runs` for any other job ID in the node, including jobs the EI was never provisioned for. This is a privilege-escalation/authorization-boundary violation: it allows unauthorized triggering of arbitrary job executions (e.g. OCR-adjacent jobs, VRF jobs, or any pipeline job configured on the node) using a credential scoped to a completely different, unrelated job. Depending on the target job's pipeline (e.g. ETHTx tasks), this could result in unauthorized transaction submission or unwanted state changes, matching a "unauthorized job/workflow execution" bounty impact class.

### Likelihood Explanation
Preconditions are minimal and match the stated attacker model: possession of one valid, legitimately-issued EI access key/secret pair (no admin/node-operator privilege required). The call sequence is a single unauthenticated-to-the-attacker HTTP call: authenticate with the EI's `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers and POST to `/v2/jobs/:jobB_ID/runs`, where `jobB_ID` is any integer job ID discoverable or guessable (job IDs are small sequential integers). This is deterministically repeatable and requires no race condition or timing dependency, making it highly feasible.

### Recommendation
In `PipelineRunsController.Create`, explicitly reject requests authenticated as an External Initiator for the int-ID run path, e.g. by checking `auth.GetAuthenticatedExternalInitiator(c)` and returning `403`/`422` if an EI principal is present, rather than relying on the overloaded `GetAuthenticatedUser` check which cannot distinguish real users from the EI's synthetic pseudo-user. Additionally, avoid overloading `SessionUserKey` with a synthetic user object in `AuthenticateExternalInitiator`; instead, have `RequiresRunRole`/callers check `SessionExternalInitiatorKey` presence directly when a distinction between EI and real-user identity matters.

### Proof of Concept
Integration test in `core/web/pipeline_runs_controller_test.go`:
1. Start an app, insert two jobs (jobA, jobB) with `RunJobV2`-runnable pipeline specs (e.g. simple non-webhook OCR-independent jobs as in `setupPipelineRunsControllerTests`).
2. Provision an External Initiator via `cltest.MustInsertExternalInitiatorWithOpts` (simulating EI scoped/intended for jobA use).
3. Build an HTTP client that sets `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` headers to the EI's credentials (mirroring `AuthenticateExternalInitiator`).
4. Send `POST /v2/jobs/<jobB.ID>/runs` using the EI credentials.
5. Assert current (vulnerable) behavior: response is `200 OK` with a `pipelineRun` resource referencing `jobB`, proving the EI credential triggered an unrelated job's run — where the expected secure behavior should be `401`/`403`/`422` rejection since EI-to-job binding was not honored.

### Citations

**File:** core/web/router.go (L449-456)
```go
	ping := PingController{app}
	userOrEI := r.Group("/v2", auth.Authenticate(app.AuthenticationProvider(),
		auth.AuthenticateExternalInitiator,
		auth.AuthenticateByToken,
		auth.AuthenticateBySession,
	))
	userOrEI.GET("/ping", ping.Show)
	userOrEI.POST("/jobs/:ID/runs", auth.RequiresRunRole(prc.Create))
```

**File:** core/web/auth/auth.go (L143-151)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})

	return nil
}
```

**File:** core/web/auth/auth.go (L177-187)
```go
// GetAuthenticatedUser extracts the authentication user from the context.
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
}
```

**File:** core/web/auth/auth.go (L202-217)
```go
func RequiresRunRole(handler func(*gin.Context)) func(*gin.Context) {
	return func(c *gin.Context) {
		user, ok := GetAuthenticatedUser(c)
		if !ok {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("not a valid session"))
			return
		}
		if user.Role == clsessions.UserRoleView {
			c.Abort()
			jsonAPIError(c, http.StatusUnauthorized, errors.New("Unauthorized"))
			return
		}
		handler(c)
	}
}
```

**File:** core/web/pipeline_runs_controller.go (L103-107)
```go
	// Webhook runs used external job UUIDs; that job type has been removed.
	if _, err := uuid.Parse(idStr); err == nil {
		jsonAPIError(c, http.StatusUnprocessableEntity, fmt.Errorf("cannot run job of type %q: %w", job.Webhook, job.ErrJobTypeRemoved))
		return
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

**File:** core/services/chainlink/application.go (L1118-1130)
```go
func (app *ChainlinkApplication) RunJobV2(
	ctx context.Context,
	jobID int32,
	meta map[string]any,
) (int64, error) {
	if build.IsProd() {
		return 0, errors.New("manual job runs not supported on secure builds")
	}
	jb, err := app.jobORM.FindJob(ctx, jobID)
	if err != nil {
		return 0, errors.Wrapf(err, "job ID %v", jobID)
	}
	var runID int64
```
