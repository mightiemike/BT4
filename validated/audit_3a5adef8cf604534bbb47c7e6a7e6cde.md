### Title
Force-approving a job proposal in the Feeds Manager service allows takeover of an existing job without ownership verification - ([File: core/services/feeds/service.go])

### Summary
The Sherlock report describes `GoatV1Pair.takeOverPool()`, where a malicious actor can "take over" an existing pool because the contract identifies a pool for replacement using loose criteria (token amounts) instead of verifying the legitimate owner/parameters, and the `force`-style penalty flow (5% burn) does not protect the original owner. The closest analogous pattern in this chainlink repo is `service.ApproveSpec()` in the Feeds Manager service, where an "existing job" that will be deleted and replaced is identified by loosely-matched fields (external job ID, contract address, capability name/version, gateway spec) rather than by verifying that the replacing proposal actually originates from the same feeds manager/owner that created the job being replaced.

### Finding Description
`ApproveSpec` resolves whether a job for the newly-approved proposal spec already exists using several match strategies, none of which validate that the entity requesting the takeover is authorized over the *existing* job: [1](#0-0) 

If a match is found, the function only checks the caller-supplied `force` boolean before deleting the pre-existing job and creating the new one in its place — there is no check that the previously-approved job "belongs" to the feeds manager/proposal now performing the takeover: [2](#0-1) 

This mirrors the `takeOverPool` root cause: identification of "the same resource" is done via externally-observable/attacker-controllable identifiers (external job ID, on-chain contract address, capability name+version, gateway job matching) rather than an ownership/ACL check tied to the feeds-manager (analogous to the "legitimate liquidity provider") that originally created the job. Ownership between feeds managers is only enforced at proposal-upsert time when the `RemoteUUID` collides with an existing proposal: [3](#0-2) 

but that check is bypassed entirely for the "existing job by address/capability" matching path used inside `ApproveSpec`, since a *different* `RemoteUUID`/proposal (i.e., a different feeds manager’s proposal) can still resolve to the same `existingJobID` via `FindJobIDByAddress`/`FindOCR2JobIDByAddress`/`FindJobIDByCapabilityNameAndVersion`/`FindGatewayJobID`.

### Impact Explanation
If a second (potentially less-trusted) feeds manager or CLO operator proposes a job spec that targets the same on-chain contract address / capability name+version / gateway configuration as an already-approved, legitimately owned job, and the approver sets `force=true`, the legitimate job is deleted and silently replaced by the new spec. This is a form of unauthorized privileged node action / job-ingestion tampering: the running OCR/OCR2/Gateway/CCIP job for a given contract or capability can be swapped out for an attacker-controlled spec, potentially causing misreporting or redirecting node execution, without any verification that the caller had rights over the original job.

### Likelihood Explanation
This requires the ability to submit job proposals through a feeds manager and to have `force=true` passed to `ApproveSpec` — this is typically an operator-controlled workflow (CLO), so exploitation depends on the deployment allowing multiple feeds managers/operators with overlapping trust boundaries, or a compromised/malicious feeds manager. It is not exploitable by a fully unauthenticated actor, so likelihood is moderate rather than certain, but the identification logic itself is the same class of flaw as the reported bug: matching by identifying attributes rather than checking legitimate ownership.

### Recommendation
When an existing job is found via `FindJobByExternalJobID`/`FindJobIDByAddress`/`FindOCR2JobIDByAddress`/`FindJobIDByCapabilityNameAndVersion`/`FindGatewayJobID` inside `ApproveSpec`, verify that the existing job is either unmanaged (self-managed, explicit local override) or managed by the *same* feeds manager as the incoming proposal before allowing `force` to delete and replace it. Cross-feeds-manager takeovers should require an explicit, separately-authorized administrative action rather than being folded into the generic `force` flag of `ApproveSpec`.

### Proof of Concept
1. Feeds manager A proposes and gets approved a job spec (e.g., OCR2 job) for contract `0xABC` — `existingJobID` is created and tracked via `FindOCR2JobIDByAddress`. [4](#0-3) 
2. Feeds manager B (different `FeedsManagerID`, different `RemoteUUID`) proposes a new job spec that also targets contract `0xABC`. The `RemoteUUID`-based ownership check in `ProposeJob` does not fire because it's a new proposal, not an update to A's proposal. [5](#0-4) 
3. When B's proposal is approved with `force=true`, `ApproveSpec` finds `existingJobID` (A's job) via `FindOCR2JobIDByAddress` matching on `0xABC`, deletes it, and creates B's job in its place — without any check that B is authorized to replace A's job. [6](#0-5)

### Citations

**File:** core/services/feeds/service.go (L778-792)
```go
	existing, err := s.orm.GetJobProposalByRemoteUUID(ctx, args.RemoteUUID)
	if err != nil {
		if !errors.Is(err, sql.ErrNoRows) {
			return 0, errors.Wrap(err, "failed to check existence of job proposal")
		}
	}

	// Validation for existing job proposals
	if err == nil {
		// Ensure that if the job proposal exists, that it belongs to the feeds
		// manager which previously proposed a job using the remote UUID.
		if args.FeedsManagerID != existing.FeedsManagerID {
			return 0, errors.New("cannot update a job proposal belonging to another feeds manager")
		}

```

**File:** core/services/feeds/service.go (L1003-1069)
```go
		// Use the external job id to check if a job already exists
		foundJob, txerr := tx.jobORM.FindJobByExternalJobID(ctx, j.ExternalJobID)
		if txerr != nil {
			// Return an error if the repository errors. If there is a not found
			// error we want to continue with approving the job.
			if !errors.Is(txerr, sql.ErrNoRows) {
				return errors.Wrap(txerr, "FindJobByExternalJobID failed")
			}
		}

		if txerr == nil {
			existingJobID = foundJob.ID
		}

		// If no job was found by external job id, check if a job exists by address
		if existingJobID == 0 {
			switch j.Type {
			case job.OffchainReporting, job.FluxMonitor:
				existingJobID, txerr = findExistingJobForOCRFlux(ctx, j, tx.jobORM)
				if txerr != nil {
					// Return an error if the repository errors. If there is a not found
					// error we want to continue with approving the job.
					if !errors.Is(txerr, sql.ErrNoRows) {
						return errors.Wrap(txerr, "FindJobIDByAddress failed")
					}
				}
			case job.OffchainReporting2, job.Bootstrap:
				existingJobID, txerr = findExistingJobForOCR2(ctx, j, tx.jobORM)
				if txerr != nil {
					// Return an error if the repository errors. If there is a not found
					// error we want to continue with approving the job.
					if !errors.Is(txerr, sql.ErrNoRows) {
						return errors.Wrap(txerr, "FindOCR2JobIDByAddress failed")
					}
				}
			case job.Workflow:
				existingJobID, txerr = tx.jobORM.FindJobIDByWorkflow(ctx, *j.WorkflowSpec)
				if txerr != nil {
					// Return an error if the repository errors. If there is a not found
					// error we want to continue with approving the job.
					if !errors.Is(txerr, sql.ErrNoRows) {
						return fmt.Errorf("failed while checking for existing workflow job: %w", txerr)
					}
				}
			case job.CCIP:
				existingJobID, txerr = tx.jobORM.FindJobIDByCapabilityNameAndVersion(ctx, *j.CCIPSpec)
				// Return an error if the repository errors. If there is a not found
				// error we want to continue with approving the job.
				if txerr != nil && !errors.Is(txerr, sql.ErrNoRows) {
					return fmt.Errorf("failed while checking for existing ccip job: %w", txerr)
				}
			case job.Gateway:
				existingJobID, txerr = tx.jobORM.FindGatewayJobID(ctx, *j.GatewaySpec)
				// Return an error if the repository errors. If there is a not found
				// error we want to continue with approving the job.
				if txerr != nil && !errors.Is(txerr, sql.ErrNoRows) {
					return fmt.Errorf("failed while checking for existing gateway job: %w", txerr)
				}
			case job.CRESettings,
				job.Stream,
				job.CCVCommitteeVerifier,
				job.CCVExecutor,
				job.StandardCapabilities:
				// NOOP: These jobs are only matched by external job ID, so do nothing
			default:
				return errors.Errorf("unsupported job type when approving job proposal specs: %s", j.Type)
			}
```

**File:** core/services/feeds/service.go (L1072-1113)
```go
		// Remove the existing job since a job was found
		if existingJobID != 0 {
			// Do not proceed to remove the running job unless the force flag is true
			if !force {
				return ErrJobAlreadyExists
			}

			// Check if the job is managed by FMS
			approvedSpec, serr := tx.orm.GetApprovedSpec(ctx, proposal.ID)
			if serr != nil {
				if !errors.Is(serr, sql.ErrNoRows) {
					logger.Errorw("Failed to get approved spec", "err", serr)

					// Return an error for any other errors fetching the
					// approved spec
					return errors.Wrap(serr, "GetApprovedSpec failed")
				}
			}

			// If a spec is found, cancel the existing job spec
			if serr == nil {
				if cerr := tx.orm.CancelSpec(ctx, approvedSpec.ID); cerr != nil {
					logger.Errorw("Failed to delete the cancel the spec", "err", cerr)

					return cerr
				}
			}

			// Delete the job
			if serr = s.jobSpawner.DeleteJob(ctx, tx.ds, existingJobID); serr != nil {
				logger.Errorw("Failed to delete the job", "err", serr)

				return errors.Wrap(serr, "DeleteJob failed")
			}
		}

		// Create the job
		if txerr = s.jobSpawner.CreateJob(ctx, tx.ds, j); txerr != nil {
			logger.Errorw("Failed to create job", "err", txerr)

			return txerr
		}
```
