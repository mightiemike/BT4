### Title
Unsanitized on-chain `WorkflowName`/`WorkflowTag` fields stored and served to the Operator UI enable stored XSS against node operators - ([File: core/services/workflows/syncer/v2/handler.go])

### Summary
When a workflow is registered on-chain via the `WorkflowRegistry` contract, any external, unprivileged address can supply an arbitrary `WorkflowName` (and `WorkflowTag`) as part of the `WorkflowRegisteredEvent`. The Chainlink node's workflow syncer ingests this event and persists the attacker-controlled name verbatim into the `job.WorkflowSpec` record, which is later exposed through the node's GraphQL API/Operator UI (`core/web/resolver/spec.go`, `core/web/presenters/job.go`). Only length checks are performed on this field — there is no character/HTML sanitization — mirroring the reported Astroport `submit_proposal` bug class where `title`/`description`/`link` were length-checked but not content-sanitized before being rendered in the official frontend.

### Finding Description
The workflow registration event handler builds the `job.WorkflowSpec` entry directly from event payload fields with no sanitization of content, only presence/format checks upstream: [1](#0-0) 

The only validation applied to workflow names anywhere in the ingestion pipeline is a length check: [2](#0-1) 

and, in the gateway HTTP trigger path, another length-only check: [3](#0-2) 

No routine escapes or strips HTML/script-relevant characters (`<`, `>`, `"`, `'`, event handler attributes, `javascript:` URIs, etc.) from `WorkflowName` or `WorkflowTag` before it is persisted as `job.WorkflowSpec.WorkflowName`/`WorkflowTag` and exposed through the node's web resolver layer (`core/web/resolver/spec.go`, `core/web/presenters/job.go`) for rendering in the Operator UI when an operator inspects registered workflow jobs. This is structurally the same defect class as the Astroport report: user-controlled strings pass only bounds/format validation at the point of ingestion, with sanitization deferred to (and dependent on) frontend code that is out of scope/unverified here.

Similarly, the older Feeds Manager job-proposal path extracts a `name` from proposed job spec TOML via a raw regex with no character filtering, and stores it for display via the `JobProposal.Name` GraphQL field: [4](#0-3) [5](#0-4) 

### Impact Explanation
If the Operator UI (or any downstream dashboard consuming these GraphQL fields) renders `WorkflowName`/`WorkflowTag`/`JobProposal.Name` without HTML-encoding, an attacker who permissionlessly registers a workflow on-chain (or a compromised/malicious Feeds Manager peer proposing a job) can inject markup/script that executes in the browser session of a node operator reviewing jobs/workflows — a stored XSS against a privileged node operator, potentially leading to session/credential theft or unauthorized node actions performed via the operator's authenticated session. Because sanitization enforcement depends entirely on the frontend (Operator UI, developed in a separate repo) rather than the backend data model, this repo cannot demonstrate the encoding is actually applied, matching the "partially solved" status of the original report.

### Likelihood Explanation
Registering a workflow on the `WorkflowRegistry` contract is a permissionless, unprivileged on-chain action available to any address, and proposing jobs through a Feeds Manager similarly does not require operator-side sanitization to succeed for the proposal to be stored. Since the current backend validation for `WorkflowName`/`WorkflowTag` is limited to length checks, exploitation likelihood is high provided the consuming frontend fails to HTML-encode these fields — a condition this repository does not itself guarantee.

### Recommendation
Add explicit character-class/format validation (e.g., allow only alphanumerics, `-`, `_`) for `WorkflowName`, `WorkflowTag`, and the Feeds Manager job proposal `name`, in addition to existing length checks, at the point of ingestion (`core/services/workflows/syncer/v2/handler.go`, `core/services/feeds/service.go`). Additionally, confirm that all frontend consumers (Operator UI) HTML-encode these values before rendering, and do not rely on backend-only length validation as an XSS mitigation.

### Proof of Concept
1. Deploy/interact with `WorkflowRegistry` and call the register function as any unprivileged address, supplying `workflowName = "<img src=x onerror=alert(document.cookie)>"` (within the 64/256-char length limit).
2. The Chainlink node's `workflowRegisteredEvent` handler processes this event and calls `createWorkflowSpec`, storing the payload's `WorkflowName` unmodified into `job.WorkflowSpec.WorkflowName` (`core/services/workflows/syncer/v2/handler.go:659-676`).
3. A node operator queries the GraphQL `spec`/job listing API (backed by `core/web/resolver/spec.go` and `core/web/presenters/job.go`), retrieving the raw, unsanitized `WorkflowName`.
4. If the Operator UI renders this value into the DOM without encoding, the injected script executes in the operator's authenticated browser session.

### Citations

**File:** core/services/workflows/syncer/v2/handler.go (L659-672)
```go
	// Create a new entry in the workflow_specs_v2 table corresponding for the new workflow, with the contents of the binaryIdentifier + configIdentifier in the table
	entry := &job.WorkflowSpec{
		Workflow:      hex.EncodeToString(decodedBinary),
		Config:        string(config),
		WorkflowID:    wfID,
		Status:        status,
		WorkflowOwner: owner,
		WorkflowName:  payload.WorkflowName,
		WorkflowTag:   payload.WorkflowTag,
		SpecType:      job.WASMFile,
		BinaryURL:     payload.BinaryURL,
		ConfigURL:     payload.ConfigURL,
		Attributes:    payload.Attributes,
	}
```

**File:** core/services/workflows/types/workflow_meta.go (L38-43)
```go
func NewWorkflowName(userDefinedName string) (WorkflowName, error) {
	if len(userDefinedName) == 0 || len(userDefinedName) > maxWorkflowNameLength {
		return nil, fmt.Errorf("invalid workflow name length: %d", len(userDefinedName))
	}
	return workflowName{userDefinedName: userDefinedName}, nil
}
```

**File:** core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go (L294-307)
```go
// validateWorkflowName validates the workflowName length and format
func (h *httpTriggerHandler) validateWorkflowName(ctx context.Context, workflowName string, requestID string, callback handlers.Callback) error {
	if len(workflowName) == 0 {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, "workflowName cannot be empty", callback)
		return errors.New("workflowName cannot be empty")
	}

	if len(workflowName) > maxWorkflowNameLength {
		h.handleUserError(ctx, requestID, jsonrpc.ErrInvalidRequest, fmt.Sprintf("workflowName cannot exceed %d characters, got %d", maxWorkflowNameLength, len(workflowName)), callback)
		return fmt.Errorf("workflowName cannot exceed %d characters, got %d", maxWorkflowNameLength, len(workflowName))
	}

	return nil
}
```

**File:** core/services/feeds/service.go (L816-826)
```go
		// Parse the Job Spec TOML to extract the name
		name := extractName(args.Spec)

		// Upsert job proposal
		id, txerr = tx.UpsertJobProposal(ctx, &JobProposal{
			Name:           name,
			RemoteUUID:     args.RemoteUUID,
			Status:         JobProposalStatusPending,
			FeedsManagerID: args.FeedsManagerID,
			Multiaddrs:     args.Multiaddrs,
		})
```

**File:** core/web/resolver/job_proposal.go (L71-74)
```go
// Name resolves to the job proposal name
func (r *JobProposalResolver) Name() *string {
	return r.jp.Name.Ptr()
}
```
