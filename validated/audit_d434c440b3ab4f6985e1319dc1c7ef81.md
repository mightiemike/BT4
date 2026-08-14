### Title
External Initiator authentication bypasses "EIs not allowed" check in PipelineRunsController.Create, enabling arbitrary job execution - ([File: core/web/auth/auth.go], [File: core/web/pipeline_runs_controller.go])

### Summary
`AuthenticateExternalInitiator` unconditionally injects a synthetic `&clsessions.User{Role: clsessions.UserRoleRun}` into the gin context for any successfully-authenticated external initiator (EI) request. Downstream code that uses the boolean form of `auth.GetAuthenticatedUser(c)` to distinguish "real user" callers from EI callers (as in `PipelineRunsController.Create`) is fooled into treating the EI as a legitimate user, defeating an explicit access-control check ("only users are allowed to run jobs using int IDs - EIs not allowed") and allowing the EI to trigger arbitrary jobs by numeric ID without any per-job/EI binding validation.

### Finding Description
`AuthenticateExternalInitiator` in `core/web/auth/auth.go` authenticates the caller strictly as an `ExternalInitiator` (via `store.FindExternalInitiator` + `bridges.AuthenticateExternalInitiator`), then sets both context keys: [1](#0-0) 

This means `GetAuthenticatedUser(c)` — which simply checks presence of `SessionUserKey` in the context — returns `ok == true` for EI-only callers, indistinguishable at that boolean level from a session/token-authenticated real user: [2](#0-1) 

`PipelineRunsController.Create` relies on exactly this boolean to gate a security-relevant branch, with a comment stating the explicit intent that EIs must not use this path: [3](#0-2) 

Because `isUser` is `true` for EI-authenticated requests too, an attacker holding only valid `bridges.ExternalInitiator` `AccessKey`/`Secret` credentials (obtained through the normal bridge/EI registration flow — no admin or user session needed) can hit `POST /v2/jobs/:ID/runs` with an arbitrary integer job ID and have `prc.App.RunJobV2(ctx, jobID, nil)` invoked directly. This path performs no check that the calling external initiator is bound to that specific job (the binding check only exists on the legitimate webhook/UUID-triggered EI run path, which is intentionally separate and is why the `idStr` int-vs-UUID branching and the "EIs not allowed" comment exist in the first place). The root cause is the hardcoded elevation at `auth.go:148`, which fabricates a well-formed-looking `*clsessions.User` (satisfying `ok` in type assertions and role checks) instead of leaving `SessionUserKey` unset for EI-only sessions, or using a dedicated principal type that call sites can distinguish from a real `clsessions.User`.

### Impact Explanation
An attacker who only has EI credentials for one specific webhook job can trigger execution of any other job in the node by its numeric job ID, bypassing the EI-to-job binding check that the webhook trigger path would normally enforce. This is unauthorized workflow/job execution — an external initiator gains the ability to run arbitrary jobs it was never provisioned to trigger, which can cause spurious on-chain transactions, unintended data submission, or resource exhaustion depending on job type. It matches the "unauthorized transaction or workflow execution" bounty impact class.

### Likelihood Explanation
Preconditions are minimal and match the threat model in the prompt: the attacker only needs a valid EI `AccessKey`/`Secret`, which is created through the ordinary bridge/EI registration flow and is not an admin/user credential. The exploit requires no race conditions, no additional bypass of signature/replay checks, and is fully reachable from an ordinary HTTP request to `POST /v2/jobs/:ID/runs` with numeric `:ID`. It is deterministic and repeatable.

### Recommendation
- Do not synthesize a `*clsessions.User` for external-initiator-only authentication. Either leave `SessionUserKey` unset for EI sessions, or introduce a distinct context key/type for the "run-role EI principal" so it cannot be mistaken for a real `clsessions.User` by callers using `auth.GetAuthenticatedUser`.
- In `PipelineRunsController.Create`, explicitly check `GetAuthenticatedExternalInitiator(c)` and reject the int-ID path when an EI principal is present, rather than relying on the presence of a `SessionUserKey` as a proxy for "is a real user."
- Audit all other call sites of `GetAuthenticatedUser` (e.g. `user_controller.go`, `webauthn_controller.go`) for the same conflation between "authenticated" and "genuine user with populated Email/role."

### Proof of Concept
Integration test in `core/web/pipeline_runs_controller_test.go` (or new test file):
1. Create Job A and register an `ExternalInitiator` bound to Job A's webhook spec (obtain `AccessKey`/`Secret`).
2. Create Job B (a separate job, e.g. of `directrequest`/`ocr` type) that the EI is not authorized to trigger.
3. Send `POST /v2/jobs/{Job B integer ID}/runs` with headers `X-Chainlink-EA-AccessKey`/`X-Chainlink-EA-Secret` set to Job A's EI credentials (no session cookie, no API token).
4. Assert the request succeeds (HTTP 200 and a `pipelineRun` resource for Job B is returned), demonstrating that the EI for Job A triggered Job B despite having no authorization for it — i.e., `isUser` was incorrectly `true` and `RunJobV2` executed without an EI-to-job binding check.
5. As a regression check post-fix, assert the same request instead returns `422 Unprocessable Entity`/`401 Unauthorized` ("bad job ID" / EIs not allowed).

### Citations

**File:** core/web/auth/auth.go (L143-148)
```go
	c.Set(SessionExternalInitiatorKey, ei)

	// External initiator endpoints (wrapped with AuthenticateExternalInitiator) inherently assume the role
	// of 'run' (required to trigger job runs)
	c.Set(SessionExternalInitiatorKey, ei)
	c.Set(SessionUserKey, &clsessions.User{Role: clsessions.UserRoleRun})
```

**File:** core/web/auth/auth.go (L178-187)
```go
func GetAuthenticatedUser(c *gin.Context) (*clsessions.User, bool) {
	obj, ok := c.Get(SessionUserKey)
	if !ok {
		return nil, false
	}

	user, ok := obj.(*clsessions.User)

	return user, ok
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
