### Title
Opted-out (PENDING_LEAVE) Universal Validators can still cast votes that are tallied against a ballot quorum computed without them - (File: x/uexecutor/keeper/msg_server.go, x/uvalidator/keeper/validator.go)

### Summary
The external report's root cause is a state/permission mismatch: an actor that has opted out of future participation (Bob's opted-out lender position) is excluded from one accounting path (borrow eligibility) but not blocked from another privileged action (rate update / withdrawal), letting stale state corrupt another party's accounting. The Push Chain analog is in the Universal Validator (UV) ballot-voting admission path: `x/uexecutor`'s `VoteInbound`/`VoteOutbound`/`VoteChainMeta` message handlers gate a voter only on Cosmos-staking "bonded" status, not on the UV's own lifecycle status, while `x/uvalidator`'s `GetEligibleVoters` — which determines the eligible-voter set and thus the ballot's `votingThreshold` — filters strictly on UV lifecycle status (`ACTIVE`/`PENDING_JOIN`).

### Finding Description
`GetEligibleVoters` in [1](#0-0)  only includes validators whose UV lifecycle status is `ACTIVE` or `PENDING_JOIN`; a validator that has been transitioned to `PENDING_LEAVE` (the "opted out" analog — set via `RemoveUniversalValidator`, [2](#0-1) ) is excluded from this set. This eligible-voter list is what `x/uexecutor` uses to compute `votesNeeded` (the ballot's `votingThreshold`) for inbound/outbound/chain-meta ballots, as seen in [3](#0-2) .

However, the message-level admission check that gates whether a given signer is even allowed to submit `MsgVoteInbound`/`MsgVoteOutbound`/`MsgVoteChainMeta` does **not** check UV lifecycle status at all — it only checks `IsBondedUniversalValidator` (a check of the underlying Cosmos-SDK staking bonded state, per the README: "the validator is in `UniversalValidatorSet`… exists in the staking module… status is `BONDED`") and `IsTombstonedUniversalValidator`: [4](#0-3) [5](#0-4) 

A validator's underlying Cosmos staking bonded status is independent of its UV lifecycle status — `RemoveUniversalValidator` moves `ACTIVE → PENDING_LEAVE` purely at the UV layer (admin action) without unbonding the base validator, per [2](#0-1) . So a `PENDING_LEAVE` validator (analogous to Bob having "opted out") remains staking-bonded and therefore still passes the `IsBondedUniversalValidator`/`IsTombstonedUniversalValidator` gate in `msg_server.go`, and its vote is forwarded into `k.VoteInbound`/`k.VoteOutbound`/`k.VoteChainMeta` for tallying — even though `GetEligibleVoters` (used to size the threshold/denominator for that very ballot) has already excluded it from the eligible set.

### Impact Explanation
If the ballot vote-recording path (`VoteOnBallot` in `x/uvalidator/keeper/voting.go`, whose full body I could not confirm in this session) does not independently re-validate that the voting signer is a member of the `eligibleVoters` snapshot passed in, a `PENDING_LEAVE` UV's vote is recorded and counted toward `yesVotes`/`noVotes` in `Ballot.IsFinalizingVote()` ( [6](#0-5) ), which is compared against a `VotingThreshold` sized from a *smaller* eligible set that excluded that same voter. This can cause a ballot (inbound mint, outbound observation, or chain-meta) to reach `PASSED`/`REJECTED` using votes from an entity outside the honest quorum the threshold was computed for — a "wrong ballot state" outcome reachable purely from an ordinary UV performing state transitions (admin removal + staking un-bond timing) without any single privileged/malicious-actor assumption beyond what an ordinary UV operator controls over their own unbonding timeline.

### Likelihood Explanation
Confirmed facts: (1) `GetEligibleVoters` filters by UV lifecycle status only; (2) `IsBondedUniversalValidator`/`IsTombstonedUniversalValidator` (the msg-server gate) check staking-layer bonded/tombstone status, not UV lifecycle status; (3) UV lifecycle transitions (`PENDING_LEAVE`) are decoupled from staking bonded/unbonded transitions. What I was **not able to verify** in this session is whether `VoteOnBallot`'s vote-recording logic (the portion of `x/uvalidator/keeper/voting.go` prior to line 197, which I did not retrieve) separately checks `voter ∈ eligibleVoters` before writing the vote into `b.Votes`. If that check exists, the invariant is preserved and this finding does not hold; if it does not exist, the mismatch above is directly exploitable by any UV operator who is admin-removed (or self-initiates `RemoveUniversalValidator`... note: only admin-only per current code) while remaining staking-bonded. Given this unresolved gap, likelihood should be treated as **unconfirmed/uncertain** pending direct inspection of `VoteOnBallot`'s voter-membership check.

### Recommendation
Add a membership/eligibility check inside `VoteOnBallot` (or in the `x/uexecutor` msg-server callers) so that a UV whose lifecycle status is not `ACTIVE`/`PENDING_JOIN` cannot have its vote recorded or tallied, regardless of its underlying staking-bonded status. Ensure the same lifecycle-status check used by `GetEligibleVoters` is the single source of truth enforced at vote-admission time, not just at threshold-sizing time. Add a property/fuzz test asserting: "a UV in `PENDING_LEAVE` can never have a vote counted toward any ballot's `yesVotes`/`noVotes`."

### Proof of Concept
Not directly demonstrable without confirming `VoteOnBallot`'s internal logic (unavailable in this session). Conceptual PoC, assuming the gap is real:
1. Admin calls `MsgRemoveUniversalValidator` on validator V while V's underlying Cosmos validator remains `BONDED` (staking is untouched by this call) — V transitions `ACTIVE → PENDING_LEAVE`.
2. `GetEligibleVoters` now excludes V; a new ballot's `votingThreshold` is computed over the reduced set.
3. V still submits `MsgVoteInbound` (or Outbound/ChainMeta); `IsBondedUniversalValidator(V)` returns true (staking bonded) and `IsTombstonedUniversalValidator(V)` returns false, so the msg-server admission succeeds.
4. If `VoteOnBallot` does not reject V as a non-member of the passed `eligibleVoters` slice, V's vote is tallied, potentially finalizing the ballot with a vote outside the intended quorum.

### Citations

**File:** x/uvalidator/keeper/validator.go (L60-94)
```go
func (k Keeper) GetEligibleVoters(ctx context.Context) ([]types.UniversalValidator, error) {
	var voters []types.UniversalValidator
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	err := k.UniversalValidatorSet.Walk(ctx, nil, func(addr sdk.ValAddress, val types.UniversalValidator) (stop bool, err error) {
		switch val.LifecycleInfo.CurrentStatus {
		case types.UVStatus_UV_STATUS_ACTIVE, types.UVStatus_UV_STATUS_PENDING_JOIN:
		default:
			return false, nil
		}

		sv, getErr := k.StakingKeeper.GetValidator(ctx, addr)
		if getErr != nil {
			// Validator removed from staking module, or some other read error:
			// treat as ineligible for this call rather than failing the whole
			// walk. This keeps quorum computable when one stranded entry would
			// otherwise crash the read path.
			k.Logger().Debug("eligible voter filter: staking GetValidator failed", "validator", addr.String(), "err", getErr)
			return false, nil
		}
		if !sv.IsBonded() {
			return false, nil
		}
		consAddr, caErr := sv.GetConsAddr()
		if caErr != nil {
			k.Logger().Debug("eligible voter filter: GetConsAddr failed", "validator", addr.String(), "err", caErr)
			return false, nil
		}
		if k.SlashingKeeper.IsTombstoned(sdkCtx, consAddr) {
			return false, nil
		}

		voters = append(voters, val)
		return false, nil
	})
```

**File:** x/uvalidator/keeper/msg_remove_universal_validator.go (L46-66)
```go
	switch val.LifecycleInfo.CurrentStatus {
	case types.UVStatus_UV_STATUS_ACTIVE:
		isOngoingTSS, err := k.UtssKeeper.HasOngoingTss(ctx)
		if err != nil {
			return fmt.Errorf("failed to check TSS state: %w", err)
		}
		if isOngoingTSS {
			return fmt.Errorf("cannot remove active validator: TSS process is ongoing")
		}

		k.Logger().Info("transitioning validator to PENDING_LEAVE",
			"validator", universalValidatorAddr,
			"old_status", oldStatus.String(),
			"new_status", types.UVStatus_UV_STATUS_PENDING_LEAVE.String(),
		)
		// Active -> Pending Leave
		if err := k.UpdateValidatorStatus(ctx, valAddr, types.UVStatus_UV_STATUS_PENDING_LEAVE, types.TransitionReason_TRANSITION_REASON_ADMIN); err != nil {
			return fmt.Errorf("failed to mark validator %s as pending leave: %w", universalValidatorAddr, err)
		}

		newStatus = types.UVStatus_UV_STATUS_PENDING_LEAVE
```

**File:** x/uexecutor/keeper/voting.go (L23-58)
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

	k.Logger().Debug("voting on inbound ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)

	// Convert []sdk.ValAddress → []string
	universalValidatorSetStrs := make([]string, len(universalValidatorSet))
	for i, v := range universalValidatorSet {
		universalValidatorSetStrs[i] = v.IdentifyInfo.CoreValidatorAddress
	}

	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
```

**File:** x/uexecutor/keeper/msg_server.go (L82-97)
```go
	// Lookup the linked universal validator for this signer
	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
	}
```

**File:** x/uexecutor/keeper/msg_server.go (L122-137)
```go
	// Lookup the linked universal validator for this signer
	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
	}
```

**File:** x/uvalidator/types/ballot.go (L134-162)
```go
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
```
