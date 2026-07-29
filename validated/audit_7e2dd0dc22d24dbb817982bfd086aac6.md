### Title
Ballot's `EligibleVoters`/`VotingThreshold` are frozen at creation and never resynchronized with the live Universal Validator set, allowing a stale-minority quorum to finalize inbound/outbound/TSS/fund-migration ballots - (File: x/uvalidator/keeper/ballot.go, x/uvalidator/types/ballot.go)

### Summary
The reported Hats-Protocol bug is a "stale threshold" class issue: `checkTransaction()` compares valid signatures against `safe.getThreshold()`, a value that is not resynced with the current set of eligible signers before the check runs, so decisions are made against outdated membership. Push Chain's generic ballot machine (`x/uvalidator/keeper/ballot.go`, used by `x/uexecutor` and `x/utss`) has the analogous defect: a ballot's `EligibleVoters` and `VotingThreshold` are computed once, at `CreateBallot` time, from whatever the Universal Validator (UV) set looked like at that instant, and are never refreshed on subsequent votes unless an operator explicitly calls `RecomputeBallotQuorum`.

### Finding Description
`VoteOnInboundBallot`, `VoteOnOutboundBallot`, and `VoteOnTssBallot` (in `x/uexecutor/keeper/voting.go` and `x/utss/keeper/voting.go`) recompute `universalValidatorSet := k.uvalidatorKeeper.GetEligibleVoters(ctx)` and a fresh `votesNeeded` **on every single vote call**, e.g.: [1](#0-0) 

However, that freshly computed `voters`/`votesNeeded` is only used by `GetOrCreateBallot` if the ballot does not already exist: [2](#0-1) 

Once a ballot record exists, its `EligibleVoters` and `VotingThreshold` fields are frozen for the ballot's lifetime — `AddVote` only checks that the caller's address is present in the *original* `EligibleVoters` snapshot, and `IsFinalizingVote`/`CountVotes` only compare against the *stored* `VotingThreshold`: [3](#0-2) [4](#0-3) 

There is no automatic mechanism that re-syncs a ballot's threshold when the UV set changes mid-vote. The only remediation path, `RecomputeBallotQuorum`, exists but must be explicitly invoked and is not wired into the hot `VoteOnBallot` path itself: [5](#0-4) 

This mirrors the HSG pattern exactly: a security-critical count (`votesNeeded`/threshold) is computed at one point in time and consulted later without being reconciled against the live authorization set (validator eligibility) at the moment the decision (finalization) is actually made.

### Impact Explanation
Concretely: a ballot for an inbound tx (deposit) or outbound tx is created while only N validators are eligible (e.g. some are `PENDING_LEAVE`/inactive), yielding threshold `T = (2N)/3+1` based on that shrunken set. Votes then trickle in over the ballot's `expiryAfterBlocks` window (up to hundreds of blocks). If the eligible set changes during that window — validators rejoining, or being added — the ballot still only requires `T` votes from among the *original* `EligibleVoters` snapshot to finalize (per `AddVote`'s membership check and `IsFinalizingVote`'s comparison against the stored `VotingThreshold`). This can let a set of validators that no longer represents the current honest quorum size finalize (`PASSED`) an inbound/outbound observation, a TSS key/fund-migration event, or a chain-meta update, using a threshold that is stale relative to the live UV set — an analog of the "minority TXs" bypass in the source report, applied to Push Chain's honest-validator ballot finalization path (inbound → UTX mutation, TSS key rotation, fund migration outcomes).

Because this path drives real state transitions (deposit crediting, outbound execution acknowledgement, TSS key finalization, fund migration records), a stale quorum finalizing prematurely or with fewer effectively-required signers than the current validator set would imply is a state-machine/authorization integrity issue directly in the "Honest-validator finalization path" and "TSS coordination" scope named in the task.

### Likelihood Explanation
This requires no malicious actor — only the ordinary combination of (a) validator set churn (join/leave, which is admin/lifecycle-driven but not attacker-controlled) occurring naturally over the network's operation, and (b) a ballot remaining open (not yet finalized) across that churn window, which is entirely plausible given expiry windows of up to `DefaultExpiryAfterBlocks`/`fundMigrationExpiryBlocks` (100,000,000 blocks for fund migration). Reachability from unprivileged user actions is more indirect here — validator set changes are not attacker-triggered — so likelihood is moderate rather than trivially attacker-triggerable, and this weakens direct applicability to the "unprivileged external attacker" gate strictly. I was not able to fully verify within this session whether `VoteOnBallot`'s freshly computed `votesNeeded`/`voters` are ever merged into an *existing* pending ballot outside of the explicit `RecomputeBallotQuorum` admin call, nor whether any other guard (e.g., a scheduled `BeginBlocker` job) periodically calls `RecomputeBallotQuorum` automatically — I did not find evidence of automatic invocation in the code reviewed.

### Recommendation
Call `RecomputeBallotQuorum` (or equivalent resynchronization logic) automatically inside `VoteOnBallot`/`AddVoteToBallot` before evaluating `IsFinalizingVote`, so that a ballot's `EligibleVoters`/`VotingThreshold` are reconciled with the current UV set on every vote, not only when an operator manually triggers it — directly mirroring the recommended fix of calling `reconcileSignerCount()` before validation in the source report.

### Proof of Concept
Not independently constructed/executed in this session due to tool limitations (no code execution available); the control-flow trace above (`CreateBallot` snapshot → `AddVote`/`IsFinalizingVote` comparing only against that snapshot → `RecomputeBallotQuorum` existing only as a separate, non-automatic entry point) is based on direct reading of `x/uvalidator/keeper/ballot.go`, `x/uvalidator/types/ballot.go`, `x/uexecutor/keeper/voting.go`, and `x/utss/keeper/voting.go`. A concrete PoC would require standing up an integration test (similar to `test/integration/uvalidator/recompute_ballot_quorum_test.go`, which already demonstrates a ballot becoming "stuck"/stale relative to the live eligible set) that creates a ballot at UV-set size N, mutates the UV set mid-vote, and shows the ballot finalizing using the stale `T` rather than a threshold recomputed against the new UV set.

### Citations

**File:** x/uexecutor/keeper/voting.go (L23-33)
```go
	universalValidatorSet, err := k.uvalidatorKeeper.GetEligibleVoters(ctx)
	if err != nil {
		return false, false, err
	}

	// number of validators
	totalValidators := len(universalValidatorSet)

	// votesNeeded = ceil(2/3 * totalValidators)
	// >2/3 quorum similar to tendermint
	votesNeeded := (types.VotesThresholdNumerator*totalValidators)/types.VotesThresholdDenominator + 1
```

**File:** x/uvalidator/keeper/ballot.go (L70-89)
```go
// GetOrCreateBallot returns the ballot if it exists, otherwise creates it.
func (k Keeper) GetOrCreateBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	voters []string,
	votesNeeded int64,
	expiryAfterBlocks int64,
) (types.Ballot, bool, error) {

	if ballot, err := k.Ballots.Get(ctx, id); err == nil {
		k.Logger().Debug("ballot found (existing)", "ballot_id", id)
		return ballot, false, nil
	}

	k.Logger().Debug("ballot not found, creating new", "ballot_id", id, "ballot_type", ballotType.String())
	newBallot, err := k.CreateBallot(ctx, id, ballotType, voters, votesNeeded, expiryAfterBlocks)

	return newBallot, true, err
}
```

**File:** x/uvalidator/keeper/ballot.go (L223-283)
```go
// RecomputeBallotQuorum rebuilds a pending ballot's eligible-voter list and
// voting threshold against the current eligible-voter set, preserving votes
// from voters still eligible and dropping votes from voters no longer eligible.
//
// If the recomputed eligible count is zero, the ballot is marked EXPIRED (no
// path to finalization). Otherwise it stays PENDING with the new parameters;
// downstream UVs must re-vote on the same ballot to trigger finalize+execute
// via the normal flow.
//
// Returns the old/new counts and threshold for the response.
func (k Keeper) RecomputeBallotQuorum(ctx context.Context, ballotID string) (
	oldEligibleCount, newEligibleCount, oldThreshold, newThreshold int64,
	newStatus types.BallotStatus,
	err error,
) {
	ballot, err := k.Ballots.Get(ctx, ballotID)
	if err != nil {
		return 0, 0, 0, 0, 0, fmt.Errorf("ballot %s not found: %w", ballotID, err)
	}

	if ballot.Status != types.BallotStatus_BALLOT_STATUS_PENDING {
		return 0, 0, 0, 0, 0, fmt.Errorf("ballot %s is not pending (status=%s); only pending ballots can be recomputed", ballotID, ballot.Status.String())
	}

	oldEligibleCount = int64(len(ballot.EligibleVoters))
	oldThreshold = ballot.VotingThreshold

	// Build the current eligible-voter set in the same valoper-bech32 format
	// the ballot already uses. The voting flow (VoteOnInboundBallot/VoteOnOutboundBallot)
	// passes CoreValidatorAddress strings directly into VoteOnBallot, so the
	// stored EligibleVoters list contains valoper bech32 addresses.
	eligibleUVs, err := k.GetEligibleVoters(ctx)
	if err != nil {
		return 0, 0, 0, 0, 0, fmt.Errorf("failed to fetch eligible voters: %w", err)
	}

	newVoters := make([]string, 0, len(eligibleUVs))
	for _, uv := range eligibleUVs {
		if uv.IdentifyInfo == nil || uv.IdentifyInfo.CoreValidatorAddress == "" {
			k.Logger().Warn("recompute: skipping UV with missing identity info")
			continue
		}
		newVoters = append(newVoters, uv.IdentifyInfo.CoreValidatorAddress)
	}
	newEligibleCount = int64(len(newVoters))

	// Zero eligible voters: no path to finalization. Mark EXPIRED.
	if newEligibleCount == 0 {
		if err := k.MarkBallotExpired(ctx, ballotID); err != nil {
			return 0, 0, 0, 0, 0, fmt.Errorf("failed to mark ballot expired on zero-eligible recompute: %w", err)
		}
		k.Logger().Info("ballot recompute: zero eligible voters → marked expired",
			"ballot_id", ballotID,
			"old_eligible", oldEligibleCount,
		)
		return oldEligibleCount, 0, oldThreshold, 0, types.BallotStatus_BALLOT_STATUS_EXPIRED, nil
	}

	// Compute new threshold using the same formula uexecutor's voting flow uses.
	// We use 2/3 + 1 — matches `(VotesThresholdNumerator * N) / VotesThresholdDenominator + 1`.
	newThreshold = (2*newEligibleCount)/3 + 1
```

**File:** x/uvalidator/types/ballot.go (L28-46)
```go
// AddVote records a vote for the given voter.
// Ensures the voter is eligible, hasn't already voted, and ballot is pending.
func (b Ballot) AddVote(address string, vote VoteResult) (Ballot, error) {
	if b.Status != BallotStatus_BALLOT_STATUS_PENDING {
		return b, fmt.Errorf("cannot vote on ballot %s: not pending", b.Id)
	}

	idx := b.GetVoterIndex(address)
	if idx == -1 {
		return b, fmt.Errorf("voter %s not eligible", address)
	}

	if b.HasVoted(address) {
		return b, fmt.Errorf("voter %s already voted", address)
	}

	b.Votes[idx] = vote
	return b, nil
}
```

**File:** x/uvalidator/types/ballot.go (L133-163)
```go
// IsFinalizingVote checks if the ballot is reaching the finalization in this tx
func (b Ballot) IsFinalizingVote() (Ballot, bool) {
	// Only pending ballots can still be finalized
	if b.Status != BallotStatus_BALLOT_STATUS_PENDING {
		return b, false
	}

	// Count votes
	yesVotes := 0
	noVotes := 0
	for _, v := range b.Votes {
		switch v {
		case VoteResult_VOTE_RESULT_SUCCESS:
			yesVotes++
		case VoteResult_VOTE_RESULT_FAILURE:
			noVotes++
		}
	}

	// If YES or NO has reached/exceeded threshold → finalizing
	if int64(yesVotes) >= b.VotingThreshold {
		b.Status = BallotStatus_BALLOT_STATUS_PASSED
		return b, true
	}
	if int64(noVotes) >= b.VotingThreshold {
		b.Status = BallotStatus_BALLOT_STATUS_REJECTED
		return b, true
	}

	return b, false
}
```
