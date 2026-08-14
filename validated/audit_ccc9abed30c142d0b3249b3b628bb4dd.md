### Title
Missing default enforcement of WorkflowDonID binding allows DON impersonation when workflowDONBindingGate is disabled - ([File: core/capabilities/remote/executable/request/server_request.go])

### Summary
`executeCapabilityRequest` only rejects a mismatch between `capabilityRequest.Metadata.WorkflowDonID` and the authenticated `callingDonID` when `workflowDONBindingGate.Limit(ctx)` returns `enabled == true`. If the gate is disabled (its documented default/rollout state), a member of legitimate calling DON A can submit a `CapabilityRequest` whose `Metadata.WorkflowDonID` references DON B, and the request will still be executed via `capability.Execute(ctx, capabilityRequest)`.

### Finding Description
The check is implemented as: [1](#0-0) 
`enabled` comes from `workflowDONBindingGate.Limit(ctx)`, and only `if enabled && capabilityRequest.Metadata.WorkflowDonID != callingDonID` triggers rejection. When `enabled` is `false`, this branch is skipped entirely and control falls through to `capability.Execute(ctx, capabilityRequest)`, passing the caller-supplied (and unauthenticated) `Metadata.WorkflowDonID` straight to the capability implementation without further validation. The `callingDonID` used for the comparison is derived from the message dispatcher/peer signature validation upstream (`e.callingDon.ID` in `OnMessage`/`executeRequest`), so DON membership of the sender is authenticated, but the `WorkflowDonID` field inside the payload is attacker-controlled application data that is not itself authenticated. This matches the pattern described in the audit question exactly: an unprivileged-but-legitimate member of DON A crafts a message with `CallerDonId=A` (so it passes DON membership/quorum checks) but `Payload.Metadata.WorkflowDonID=B`.

### Impact Explanation
If a capability implementation trusts `Metadata.WorkflowDonID` for authorization, billing, secret/vault scoping, or trigger routing decisions (as suggested by the comment referencing billing/scoping and the existence of `zone_b_restriction.go` and vault flows that also key off `WorkflowDonID`), a malicious-but-legitimate member of DON A could cause capability actions to be attributed to, billed to, or scoped under DON B — a privilege escalation / workflow impersonation across DON boundaries. The severity depends on which downstream capabilities key their access control or billing purely on this field, which is gated by rollout flag rather than enforced unconditionally.

### Likelihood Explanation
This requires only that: (1) the `workflowDONBindingGate` is in its disabled state (explicitly called out in code comments and config file `workflow-gateway-capabilities-don-vault-workflow-don-binding-enabled.toml`, implying it is an opt-in/rollout feature, so disabled is plausibly the default elsewhere), and (2) the attacker is a legitimate member of some DON who can submit a normal `MessageBody` with mismatched metadata — both of which are within the stated unprivileged threat model. No key compromise or admin privilege is required.

### Recommendation
Enforce the `WorkflowDonID == callingDonID` invariant unconditionally (fail-closed), removing the `enabled &&` short-circuit, or invert the rollout so that the binding check defaults to enabled and the gate is used only to temporarily *disable* it during a controlled migration with monitoring — not as a security-relevant off switch. At minimum, downstream capability implementations must not trust `Metadata.WorkflowDonID` for authorization/billing without independent validation against the authenticated `callingDonID`.

### Proof of Concept
Unit test targeting `executeCapabilityRequest` in `core/capabilities/remote/executable/request/server_request_test.go` (or `server_request_binding_test.go`, which already tests binding behavior):
1. Construct a fake `limits.GateLimiter` whose `Limit(ctx)` returns `(false, nil)`.
2. Build a `CapabilityRequest` payload with `Metadata.WorkflowDonID = donB` while `callingDonID = donA` (donA != donB).
3. Call `executeCapabilityRequest(ctx, lggr, capability, payload, donA, gate)`.
4. Assert that `capability.Execute` is invoked (e.g., via a mock capability) and no error referencing WorkflowDonID mismatch is returned — demonstrating the request proceeds despite the mismatch.
5. Repeat with the gate returning `(true, nil)` to confirm the same request is correctly rejected, proving the enforcement is solely contingent on the gate state.

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
