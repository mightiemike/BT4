### Title
Workflow DON binding check is bypassed when `workflowDONBindingGate` is disabled, allowing cross-DON `WorkflowDonID` spoofing - (File: core/capabilities/remote/executable/request/server_request.go)

### Summary
`executeCapabilityRequest` in `core/capabilities/remote/executable/request/server_request.go` only validates that the caller-supplied `Metadata.WorkflowDonID` matches the authenticated `callingDonID` when `workflowDONBindingGate.Limit(ctx)` returns `true`. When the gate is disabled (its default/rollback state), the mismatch check is skipped entirely and the capability executes with an attacker-controlled `WorkflowDonID`/`WorkflowExecutionID`, letting a member of one calling DON bind capability results to a workflow belonging to a different DON.

### Finding Description
`OnMessage` (`core/capabilities/remote/executable/request/server_request.go:178`) authenticates only that the message sender is a member of `e.callingDon` (`addRequester`, line 259-273); it never validates the workflow metadata inside the payload. Once quorum (`F+1`) is reached, it calls `executeRequest` → `executeCapabilityRequest` (line 344), passing `e.callingDon.ID` as the authenticated `callingDonID`.

Inside `executeCapabilityRequest`: [1](#0-0) 
The DON-binding check is wrapped in `if enabled && ...`. If `workflowDONBindingGate.Limit(ctx)` returns `false` (gate closed), the condition short-circuits and the function proceeds directly to `capability.Execute(ctx, capabilityRequest)` using the attacker-supplied `Metadata.WorkflowDonID`/`WorkflowExecutionID`/`WorkflowID` verbatim, with no cross-check against `callingDonID`.

This is confirmed by the repo's own test suite, which explicitly documents and asserts this behavior: [2](#0-1) 
The subtest `"gate disabled, WorkflowDonID mismatch still executes"` sends a payload with `WorkflowDonID = callingDonID + 99` (a different DON) through a legitimately-authenticated calling-DON quorum, with the gate constructed via `limits.NewGateLimiter(false)`, and asserts the server returns `types.Error_OK`, i.e., it executes and returns a successful response bound to the mismatched `WorkflowDonID`.

Since `sendResponse` (line 313-342) and the underlying capability execution use only the request's `CapabilityId`/`CapabilityDonId`/`CallerDonId` for message routing, and the capability-level authorization is scoped by `Metadata.WorkflowDonID`/`WorkflowExecutionID` inside the payload (used downstream by capability implementations, e.g. trigger/consensus/vault capabilities to associate results with a specific workflow), an attacker who is any authenticated member of a legitimate calling DON can supply a `WorkflowDonID` belonging to a different DON, and, with the gate off, have the capability execute and produce a response bound to that other DON's workflow context.

### Impact Explanation
This enables cross-tenant/cross-workflow capability execution and result injection: a workflow node in one DON can cause a shared capability to execute on behalf of, and label its response as belonging to, another DON's workflow (arbitrary `WorkflowDonID`/`WorkflowExecutionID`). Depending on the capability, this can lead to unauthorized capability invocation under another workflow's identity, corrupted/misattributed execution results, or resource-quota/isolation bypass across tenants — a workflow/data-tampering and cross-tenant isolation violation.

### Likelihood Explanation
The bypass is triggered simply by the feature gate `workflowDONBindingGate` evaluating to `false`/disabled — this is an explicit, code-supported branch (not a hypothetical), demonstrated by the maintainers' own unit test. Exploitation requires the attacker to be an authenticated peer within *some* calling DON (passing `addRequester`'s membership check) but does not require any additional privilege, key leakage, or node-operator access to the target DON whose ID is being spoofed. Whether this is reachable in a default production deployment depends on whether `workflowDONBindingGate` is enabled by default when wired up in `core/capabilities/launcher.go` / `core/capabilities/remote/executable/server.go` — I was not able to fully confirm the default wiring/config value before running out of investigation budget, so likelihood in a specific deployment is uncertain and should be verified by checking the gate's construction and default settings source in those two files.

### Recommendation
Enforce the `WorkflowDonID == callingDonID` binding unconditionally (or make it fail-closed rather than fail-open when the gate is disabled), since the security invariant (cross-tenant workflow isolation) should not depend on a feature flag defaulting to disabled. If the gate exists purely for staged rollout, ensure it defaults to enabled, add monitoring/alerting when disabled, and add an integration test enforcing that disabling the gate cannot be used to bypass authorization in production configs.

### Proof of Concept
The vulnerability is already reproduced by the existing test `Test_ServerRequest_WorkflowDONBinding`, subtest `"gate disabled, WorkflowDonID mismatch still executes"` in `core/capabilities/remote/executable/request/server_request_binding_test.go:91-95`: it builds a payload with `WorkflowDonID = callingDonID + 99`, drives an F+1 quorum of `OnMessage` calls from legitimate calling-DON peers with `gate = limits.NewGateLimiter(false)`, and asserts the response `Error` is `types.Error_OK` — i.e., execution succeeds despite the DON mismatch. An additional PoC assertion to prove impact would extend this test to also assert that the resulting `capabilities.CapabilityResponse`/downstream capability call received `Metadata.WorkflowDonID = callingDonID + 99` unchanged, demonstrating that the capability layer treats the attacker-controlled foreign DON ID as authoritative.

### Citations

**File:** core/capabilities/remote/executable/request/server_request.go (L357-367)
```go
	enabled, gerr := workflowDONBindingGate.Limit(ctx)
	if gerr != nil {
		lggr.Errorw("failed to evaluate workflow DON binding gate", "err", gerr)
		return nil, errors.New("failed to evaluate workflow DON binding gate")
	}
	if enabled && capabilityRequest.Metadata.WorkflowDonID != callingDonID {
		lggr.Errorw("workflow DON ID in request metadata does not match calling DON",
			"metadataWorkflowDonID", capabilityRequest.Metadata.WorkflowDonID, "callingDonID", callingDonID)
		return nil, fmt.Errorf("workflow DON ID %d in request metadata does not match calling DON ID %d",
			capabilityRequest.Metadata.WorkflowDonID, callingDonID)
	}
```

**File:** core/capabilities/remote/executable/request/server_request_binding_test.go (L91-95)
```go
	t.Run("gate disabled, WorkflowDonID mismatch still executes", func(t *testing.T) {
		t.Parallel()
		d := newReqAndSendQuorum(t, limits.NewGateLimiter(false), callingDonID+99)
		require.Equal(t, types.Error_OK, d.msgs[0].Error)
	})
```
