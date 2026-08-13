### Title
Workflow name identity resolution relies on an unsalted, 80-bit truncated hash, enabling a birthday-style collision to hijack workflow routing/authorization - (File: core/services/workflows/types/workflow_meta.go, core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go, core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go)

### Summary
Chainlink's workflow identity system reduces a user-supplied `WorkflowName` string to a 10-byte (80-bit) hash via `pkgworkflows.HashTruncateName()` and uses this truncated hash as the "name" component of the composite key `(workflowOwner, workflowName, workflowTag)` that resolves to a `workflowID` for gateway routing and for granting execution authorization (via `authorizedKeys`). This mirrors the C4 "validateCallback birthday attack" bug class: a security-relevant identifier is derived by truncating a cryptographic hash without any subsequent verification against the full/canonical value, so two different inputs (workflow names) can collide in the truncated space and be treated as the same identity.

### Finding Description
`WorkflowName.Hex()` truncates the name hash to 10 bytes and documents that this value is "used in the metadata we send onchain and for authorizing the workflow with the consumer contract": [1](#0-0) 

The gateway's HTTP trigger flow resolves a `workflowID` purely from `(workflowOwner, HashTruncateName(workflowName), workflowTag)` when no `workflowID` is given directly: [2](#0-1) 

`WorkflowMetadataHandler.GetWorkflowID` performs a bare map lookup on this composite key, with no re-verification against the original workflow name, and `authorizedKeys` (the JWT-signer allowlist that grants execution authorization) is keyed off the resulting `workflowID`: [3](#0-2) [4](#0-3) 

Analogous to the C4-05 finding, the truncated value is treated as a unique, collision-resistant identity without checking the full pre-image; here it's the workflow name hash (80 bits) rather than a Uniswap pool address (160 bits). Because 80 bits requires only ~2^40 hash attempts for a 50% collision probability (versus 2^160/2^80 in the original report), this is a *much cheaper* birthday attack — computable in commodity time on a single GPU rather than requiring a multi-million-dollar ASIC farm.

The same truncation is also used as an on-chain identity guard: `AutomationReceiver.setExpectedWorkflowName` stores only the truncated 10-byte hash of the "expected" workflow name, and the receiver's inbound report-authorization check (`WorkflowIdentityNotConfigured`) is gated on `(expectedAuthor, truncatedNameHash)` matching: [5](#0-4) [6](#0-5) 

### Impact Explanation
If an attacker who controls a workflow owner address that is permitted to register additional workflows under that same owner (e.g., a shared/org-level owner address, or an owner whose key the attacker otherwise controls but which is trusted for one *specific* named workflow by a downstream authorization gate) can register a new workflow whose name differs from the legitimate/expected workflow name but whose `HashTruncateName` output collides in the 80-bit space, the gateway's `workflowRefToID`/`authorizedKeys` map and the on-chain `AutomationReceiver` identity guard would treat the attacker's new workflow as the pre-authorized one. This could let a malicious workflow:
- Have its trigger requests resolved to (and inherit authorization for) a different, previously-approved `workflowID`/JWT signer set in the gateway's HTTP trigger flow.
- Have its automation reports accepted by an `AutomationReceiver` that is gated on `(expectedAuthor, expectedWorkflowNameHash)`, bypassing the intended "only this specific workflow can drive this receiver" guarantee, potentially resulting in unauthorized privileged calls (`performUpkeep`-style calldata) to whatever target the receiver is allowed to call.

### Likelihood Explanation
Finding an 80-bit collision requires ~2^40 hash computations for a 50% success probability — trivially achievable with commodity GPU compute (billions of hashes/sec), unlike the original 160-bit finding which required a multi-million-dollar, multi-year compute effort. This makes the underlying bug class significantly more practical here. However, exploitability is bounded by the requirement that the attacker be able to register a *new* workflow under the *same* owner address already trusted/expected by the target authorization mechanism (gateway `authorizedKeys` map or `AutomationReceiver.expectedAuthor`), which limits this to scenarios where owner addresses are shared across workflows of differing trust levels (a plausible but not universally-applicable operational pattern for CRE/workflow deployments).

### Recommendation
Do not rely solely on a truncated hash as a unique identity/authorization key. Either:
1. Store/compare the full (untruncated) hash or the full workflow name string for authorization-critical comparisons (gateway `workflowRefToID`, `authorizedKeys`, and the on-chain `AutomationReceiver` identity guard), reserving the 10-byte truncated form only for display/non-security-critical metadata, or
2. If the 10-byte (or 20-byte contract-size-constrained) form must be retained for on-chain storage efficiency, widen it enough to make birthday attacks infeasible (e.g., ≥16 bytes/128 bits) and additionally require that authorization checks re-derive/re-validate the truncated hash from a stored full name rather than trusting a caller-supplied name plus a matching truncated hash alone.

### Proof of Concept
Not independently reproduced (no live environment available); this analysis is based on static code inspection. Conceptually:
1. Legitimate owner `O` registers workflow `W1` named `"prod-eth-balance-monitor"`; the AutomationReceiver's `expectedWorkflowName` is set to `HashTruncateName("prod-eth-balance-monitor")` (10 bytes) alongside `expectedAuthor = O`.
2. An attacker who can register additional workflows under owner `O` (or otherwise cause registration under that owner) brute-forces candidate strings offline until finding `W2` such that `HashTruncateName(W2) == HashTruncateName("prod-eth-balance-monitor")` — feasible at ~2^40 hash attempts.
3. Attacker deploys/registers `W2` under owner `O`; gateway `workflowRefToID`/`GetWorkflowID` and the `AutomationReceiver`'s inbound identity check both compare only the truncated hash, so `W2`'s reports/trigger requests are treated as authorized under the identity meant exclusively for `W1`.

### Citations

**File:** core/services/workflows/types/workflow_meta.go (L13-32)
```go
type WorkflowName interface {
	// A 10-byte hash of the name, hex-encoded to a 20-byte string.
	// Used in the metadata we send onchain and for authorizing
	// the workflow with the consumer contract.
	Hex() string

	// User-defined workflow name has can be of any length between
	// 1 and maxWorkflowNameLength. Used for logging and metrics.
	String() string
}

type workflowName struct {
	userDefinedName string
}

func (n workflowName) Hex() string {
	truncatedName := pkgworkflows.HashTruncateName(n.userDefinedName)
	hexName := hex.EncodeToString([]byte(truncatedName))
	return hexName
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L333-357)
```go
func (h *httpTriggerHandler) resolveWorkflowID(ctx context.Context, triggerReq *jsonrpc.Request[gateway_common.HTTPTriggerRequest], requestID string, callback handlers.Callback) (string, error) {
	h.lggr.Debugw("resolving workflow ID", "workflowID", triggerReq.Params.Workflow.WorkflowID, "workflowOwner", triggerReq.Params.Workflow.WorkflowOwner, "workflowName", triggerReq.Params.Workflow.WorkflowName, "workflowTag", triggerReq.Params.Workflow.WorkflowTag, "requestID", requestID)
	workflowID := triggerReq.Params.Workflow.WorkflowID
	if workflowID != "" {
		workflowID = normalizeHex(workflowID, workflowIDLength)
		_, found := h.workflowMetadataHandler.GetWorkflowReference(workflowID)
		if !found {
			h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, fmt.Sprintf("Workflow not found. 'workflowID' %s is not a valid workflow ID", workflowID), callback)
			return "", errors.New("workflow not found")
		}
		return workflowID, nil
	}
	workflowOwner := normalizeHex(triggerReq.Params.Workflow.WorkflowOwner, workflowOwnerLength)
	workflowName := "0x" + hex.EncodeToString([]byte(workflows.HashTruncateName(triggerReq.Params.Workflow.WorkflowName)))
	workflowID, found := h.workflowMetadataHandler.GetWorkflowID(
		workflowOwner,
		workflowName,
		triggerReq.Params.Workflow.WorkflowTag,
	)
	if !found {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "Workflow not found. Provide either a valid 'workflowID' or a valid combination of 'workflowOwner', 'workflowName', and 'workflowTag'", callback)
		return "", errors.New("workflow not found")
	}
	return workflowID, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L105-140)
```go
// syncMetadata aggregates the authorized keys and workflow selectors from the WorkflowMetadataAggregator and updates the local cache.
// Should be called periodically to keep the authorized keys up to date.
func (h *WorkflowMetadataHandler) syncMetadata(ctx context.Context) {
	metadata, err := h.agg.Aggregate()
	if err != nil {
		h.lggr.Errorw("Failed to aggregate auth data", "error", err)
		return
	}
	authorizedKeys := make(map[string]map[gateway.AuthorizedKey]struct{})
	workflowRefToID := make(map[workflowReference]string)
	workflowIDToRef := make(map[string]workflowReference)
	for _, data := range metadata {
		workflowRef := workflowReference{
			workflowOwner: data.WorkflowSelector.WorkflowOwner,
			workflowName:  data.WorkflowSelector.WorkflowName,
			workflowTag:   data.WorkflowSelector.WorkflowTag,
		}
		// Only the first aggregated workflow reference is used because
		// workflow reference is unique (enforced by workflow registry)
		// workflow reference and workflow ID mapping in the gateway eventually becomes consistent
		// with the mapping on-chain
		if _, exists := workflowIDToRef[data.WorkflowSelector.WorkflowID]; exists {
			h.lggr.Debug("Duplicate workflow ID found", "workflowID", data.WorkflowSelector.WorkflowID)
			continue
		}
		if _, exists := workflowRefToID[workflowRef]; exists {
			h.lggr.Debugw("Duplicate workflow reference found", "workflowRef", workflowRef, "workflowID", data.WorkflowSelector.WorkflowID)
			continue
		}
		workflowIDToRef[data.WorkflowSelector.WorkflowID] = workflowRef
		workflowRefToID[workflowRef] = data.WorkflowSelector.WorkflowID
		authorizedKeys[data.WorkflowSelector.WorkflowID] = make(map[gateway.AuthorizedKey]struct{})
		for _, key := range data.AuthorizedKeys {
			authorizedKeys[data.WorkflowSelector.WorkflowID][key] = struct{}{}
		}
	}
```

**File:** core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go (L300-313)
```go
func (h *WorkflowMetadataHandler) GetWorkflowID(workflowOwner, workflowName, workflowTag string) (string, bool) {
	h.mu.RLock()
	defer h.mu.RUnlock()
	workflowRef := workflowReference{
		workflowOwner: workflowOwner,
		workflowName:  workflowName,
		workflowTag:   workflowTag,
	}
	workflowID, exists := h.workflowRefToID[workflowRef]
	if !exists {
		return "", false
	}
	return workflowID, true
}
```

**File:** deployment/vault/changeset/types/types.go (L201-217)
```go
// AutomationReceiverChainConfig is deployment-time configuration for AutomationReceiver on one chain.
type AutomationReceiverChainConfig struct {
	// ForwarderAddress is the CRE forwarder address passed to the AutomationReceiver constructor.
	ForwarderAddress string `json:"forwarderAddress"`
	// TargetAddress is the contract AR is allowed to call via setCallAllowed (e.g. an EthBalMon address).
	TargetAddress string `json:"targetAddress"`
	// Selector is the 4-byte function selector as a hex string (e.g. "0x4b9f5c20").
	// Defaults to performUpkeep(bytes) if empty.
	Selector string `json:"selector,omitempty"`
	// ExpectedAuthor / ExpectedWorkflowName lock the AutomationReceiver's inbound identity
	// guard at deploy time. The receiver reverts with WorkflowIdentityNotConfigured until an
	// identity is set, so configuring it here makes the receiver usable right after deploy.
	// Optional: set BOTH (recommended, stable across workflow redeploys) or leave both empty
	// to skip identity setup at deploy time.
	ExpectedAuthor       string `json:"expectedAuthor,omitempty"`
	ExpectedWorkflowName string `json:"expectedWorkflowName,omitempty"`
}
```

**File:** deployment/vault/changeset/automation_receiver_set_expected_workflow_identity.go (L22-27)
```go
// SetExpectedWorkflowIdentityChangeSet builds a timelock proposal to configure the
// AutomationReceiver's inbound identity guard (setExpectedAuthor + setExpectedWorkflowName) on
// already-deployed receivers whose ownership is the Timelock. The AutomationReceiver reverts
// inbound reports with WorkflowIdentityNotConfigured until the identity is configured, so this is
// required for a deployed receiver to accept reports. Author + name are stable across workflow
// redeploys, so this only needs to be set once (unlike pinning the workflow id).
```
