### Title
Unbounded ballot-expiry scan on every new ballot lets an attacker cheaply grow `ActiveBallotIDs` and inflate the cost/time of every subsequent vote transaction, degrading block processing — ([File: x/uvalidator/keeper/ballot.go])

### Summary
`x/uvalidator`'s generic ballot machine walks the *entire* `ActiveBallotIDs` set synchronously every time a brand-new ballot is created (`GetOrCreateBallot` → `CreateBallot` → `ExpireBallotsBeforeHeight`). This is the same bug class as Sherlock H-8: a queue/collection is processed in full, with no batching or cap, and an attacker can cheaply grow that collection through ordinary, unprivileged, attacker-controlled activity (spamming distinct cross-chain events that honest UVs must vote on), making the per-operation cost scale with the number of outstanding entries.

### Finding Description
`ExpireBallotsBeforeHeight` iterates every ID currently in `k.ActiveBallotIDs` and performs a `k.Ballots.Get` for each one to check whether it should expire: [1](#0-0) 

This function is invoked unconditionally by `CreateBallot` before every new ballot is stored: [2](#0-1) 

`CreateBallot` is reached from `GetOrCreateBallot`, which is called on the first vote for *any* new observation: [3](#0-2) 

`GetOrCreateBallot`/`CreateBallot`/`ExpireBallotsBeforeHeight` sit behind `VoteOnBallot`, the single mechanism used by every crosschain observation path (`x/uexecutor` inbound/outbound votes, chain-meta, and `x/utss` TSS events) — i.e. `ActiveBallotIDs` is one **global, shared** collection across all ballot types: [4](#0-3) [5](#0-4) 

The ballot ID for an inbound observation is derived directly from attacker-controlled event data (`GetInboundBallotKey`), so an unprivileged attacker who sends many distinct, cheap transactions to a supported source-chain gateway (no minimum-amount restriction enforced pre-ballot) forces honest UVs to submit `MsgVoteInbound` for each distinct observation: [6](#0-5) [7](#0-6) 

Every one of these first-votes creates a brand-new ballot, and each such creation re-scans the *entire* set of currently-active ballots (which, in a healthy chain, includes not just inbound ballots but also outbound, chain-meta, TSS, and migration ballots awaiting quorum or expiry). As the attacker keeps generating distinct events faster than the fixed `expiryAfterBlocks` window clears them, `ActiveBallotIDs` grows unboundedly, and the per-vote cost (one KV read per active ballot) grows linearly with it — imposed on every subsequent vote from every honest UV, for every ballot type, not just the attacker's own inbound stream.

This directly mirrors the Sherlock H-8 root cause: a "remove/expire matured entries" loop with no minimum-value gate on the triggering action and no batching/pagination, letting an attacker turn a small, cheap, repeatable action into unbounded per-block/per-operation work for the whole validator set.

### Impact Explanation
As the number of active ballots grows, every vote message (`MsgVoteInbound`, `MsgVoteOutbound`, TSS/chain-meta votes) becomes progressively more expensive to process (linear KV-store reads), slowing transaction execution and increasing the chance that vote transactions run out of gas or push blocks toward gas/time limits. Because this happens inside ordinary message handling (not privileged code), it can degrade the liveness of crosschain finalization broadly — inbound execution, outbound observation, and TSS/migration ballot processing all share the same `ActiveBallotIDs` collection, so spam in one ballot type degrades the others. This matches the "slow down / halt the chain" impact category and the in-scope "denial of service ... reachable without privileged control."

### Likelihood Explanation
Likelihood is moderate-to-high assuming validators are honest but must faithfully vote on all observed source-chain events per protocol design; the attacker only needs to submit many distinct low-cost transactions on a supported external chain to force UVs into a growing number of `MsgVoteInbound` calls. Sending distinct transactions is not gated by any minimum value check prior to ballot creation. The severity depends on the actual expiry window (`DefaultExpiryAfterBlocks`) and per-ballot storage/read cost; I was not able to fully verify the exact value of `DefaultExpiryAfterBlocks` or measure the real per-ballot cost within the tool budget, so the magnitude of degradation (vs. a hard halt) is not fully quantified here.

### Recommendation
- Cap/paginate `ExpireBallotsBeforeHeight` so it processes only a bounded number of stale ballots per call (or move expiry sweeping to a scheduled/rate-limited job rather than doing a full scan on every `CreateBallot`).
- Consider tracking active ballots per expiry-height bucket (e.g., a time-ordered index) so expiry lookups are O(1)/O(log n) instead of O(active ballots).
- Add economic/anti-spam gating on cheap, attacker-triggerable inbound events (e.g., minimum value, rate limiting per source address, or per-source-chain throttling) before they are allowed to create new ballots.

### Proof of Concept
Not independently executed; based on static code tracing:
1. Attacker sends N distinct low-value/garbage transactions to a Push Chain gateway contract on a supported external chain, each producing a unique `(source_chain, tx_hash, log_index)` tuple.
2. Honest UVs observe each event and submit `MsgVoteInbound`, each hitting a distinct `ballotID` (`GetInboundBallotKey`), so each vote triggers `GetOrCreateBallot` → `CreateBallot` → `ExpireBallotsBeforeHeight`.
3. As N grows faster than `DefaultExpiryAfterBlocks` clears entries, `ActiveBallotIDs` grows to size N, and each subsequent `CreateBallot` call (from any ballot type, not just inbound) does an O(N) walk with a KV read per active ballot.
4. Repeat/scale N until per-transaction processing cost measurably slows vote throughput across all ballot types sharing `ActiveBallotIDs`.

### Citations

**File:** x/uvalidator/keeper/ballot.go (L12-39)
```go
// CreateBallot creates a new ballot with the given parameters, stores it, and marks it as active.
func (k Keeper) CreateBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	eligibleVoters []string,
	votingThreshold int64,
	expiryAfterBlocks int64,
) (types.Ballot, error) {
	// Get current block height
	blockHeight, err := k.GetBlockHeight(ctx)
	if err != nil {
		return types.Ballot{}, err
	}

	k.Logger().Debug("creating ballot",
		"ballot_id", id,
		"ballot_type", ballotType.String(),
		"eligible_voters", len(eligibleVoters),
		"voting_threshold", votingThreshold,
		"expiry_after_blocks", expiryAfterBlocks,
		"block_height", blockHeight,
	)

	// First, expire any old ballots before this height
	if err := k.ExpireBallotsBeforeHeight(ctx, blockHeight); err != nil {
		return types.Ballot{}, err
	}
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

**File:** x/uvalidator/keeper/ballot.go (L320-347)
```go
// ExpireBallotsBeforeHeight checks active ballots and marks expired ones.
// It uses a two-phase approach: first collect IDs to expire, then mutate,
// to avoid modifying the ActiveBallotIDs collection during iteration.
func (k Keeper) ExpireBallotsBeforeHeight(ctx context.Context, currentHeight int64) error {
	iter, err := k.ActiveBallotIDs.Iterate(ctx, nil)
	if err != nil {
		return err
	}

	// Phase 1: collect IDs to expire
	var toExpire []string
	for ; iter.Valid(); iter.Next() {
		id, err := iter.Key()
		if err != nil {
			iter.Close()
			return err
		}

		ballot, err := k.Ballots.Get(ctx, id)
		if err != nil {
			iter.Close()
			return err
		}

		if ballot.BlockHeightExpiry <= currentHeight {
			toExpire = append(toExpire, id)
		}
	}
```

**File:** x/uvalidator/README.md (L32-55)
```markdown
### Generic Ballot Machine

Every crosschain observation (in `x/uexecutor` and `x/utss`) is voted through this single mechanism:

```go
ballot, finalized, isNew, err := k.VoteOnBallot(
    ctx,
    ballotId,           // canonical hash of the observation
    ballotType,         // INBOUND | OUTBOUND | CHAIN_META | TSS_EVENT | FUND_MIGRATION
    voter,              // signer's bech32 address
    voteResult,         // SUCCESS | FAILURE
    eligibleVoters,     // snapshot of UVs at ballot creation
    votesNeeded,        // threshold (caller decides 2/3, 100%, simple majority, ...)
    expiryAfterBlocks,  // ballot auto-expires after this many blocks
)
```

A ballot is created lazily on the first vote, indexed in `ActiveBallotIDs`, and finalizes the moment either:
- `yesVotes >= votingThreshold` -> `BALLOT_STATUS_PASSED`
- `eligibleVoters - noVotes < votingThreshold` (the threshold is now mathematically unreachable) -> `BALLOT_STATUS_REJECTED`

On finalization, the ballot is moved from `ActiveBallotIDs` -> `FinalizedBallotIDs`. Expired ballots that never reached threshold are moved to `ExpiredBallotIDs`.

The ballot type is opaque — `x/uvalidator` doesn't care what's being voted on. The ballot ID is a `sha256` of the canonical observation, so two validators voting on the same observation hit the same ballot deterministically.
```

**File:** x/uvalidator/keeper/voting.go (L120-184)
```go
func (k Keeper) VoteOnBallot(
	ctx context.Context,
	id string,
	ballotType types.BallotObservationType,
	voter string,
	voteResult types.VoteResult,
	voters []string,
	votesNeeded int64,
	expiryAfterBlocks int64,
) (
	ballot types.Ballot,
	isFinalized bool,
	isNew bool,
	err error) {

	k.Logger().Debug("vote on ballot",
		"ballot_id", id,
		"ballot_type", ballotType.String(),
		"voter", voter,
		"vote_result", voteResult.String(),
		"votes_needed", votesNeeded,
	)

	ballot, isNew, err = k.GetOrCreateBallot(ctx, id, ballotType, voters, votesNeeded, expiryAfterBlocks)
	if err != nil {
		return ballot, false, false, errors.Wrap(err, "Error while voting on the ballot")
	}

	// reject votes on ballots whose nominal expiry has passed
	currentHeight := sdk.UnwrapSDKContext(ctx).BlockHeight()
	if ballot.IsExpired(currentHeight) {
		// Transition PENDING ballots to EXPIRED so subsequent reads see the
		// canonical status and the secondary indexes stay consistent. Already
		// non-PENDING ballots fall through to the Status check below for the
		// existing "already X" error message.
		if ballot.Status == types.BallotStatus_BALLOT_STATUS_PENDING {
			if mErr := k.MarkBallotExpired(ctx, id); mErr != nil {
				return ballot, false, isNew, errors.Wrap(mErr, "failed to mark ballot expired during late-vote rejection")
			}
			k.Logger().Warn("late vote rejected, ballot marked expired",
				"ballot_id", id,
				"expiry_height", ballot.BlockHeightExpiry,
				"current_height", currentHeight,
				"voter", voter,
			)
			return ballot, false, isNew, fmt.Errorf("ballot %s expired at height %d (current %d)", id, ballot.BlockHeightExpiry, currentHeight)
		}
	}

	if ballot.Status != types.BallotStatus_BALLOT_STATUS_PENDING {
		k.Logger().Warn("ballot is not in pending state, cannot vote",
			"ballot_id", id,
			"ballot_status", ballot.Status.String(),
			"voter", voter,
		)
		return ballot, false, false, fmt.Errorf("ballot %s is already %s", id, ballot.Status.String())
	}

	if isNew {
		k.Logger().Debug("created new ballot", "ballot_id", id, "ballot_type", ballotType.String())
		err := k.ActiveBallotIDs.Set(ctx, id)
		if err != nil {
			return ballot, false, false, errors.Wrap(err, "Error while voting on the ballot")
		}
	}
```

**File:** x/uexecutor/keeper/voting.go (L11-58)
```go
func (k Keeper) VoteOnInboundBallot(
	ctx context.Context,
	universalValidator sdk.ValAddress,
	inbound types.Inbound,
) (isFinalized bool,
	isNew bool,
	err error) {
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return false, false, err
	}

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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L18-66)
```go
func (k Keeper) VoteInbound(ctx context.Context, universalValidator sdk.ValAddress, inbound types.Inbound) error {
	// Canonicalize first so every derived key + the stored inbound use one
	// representation per logical event.
	inbound.Canonicalize()

	k.Logger().Info("vote inbound received",
		"validator", universalValidator.String(),
		"source_chain", inbound.SourceChain,
		"tx_hash", inbound.TxHash,
		"tx_type", inbound.TxType.String(),
		"sender", inbound.Sender,
	)

	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Step 1: Derive UTX key from the original inbound data (source_chain:tx_hash:log_index)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)
	found, err := k.HasUniversalTx(ctx, universalTxKey)
	if err != nil {
		return errors.Wrap(err, "failed to check UniversalTx")
	}
	if found {
		k.Logger().Warn("vote inbound rejected: utx already exists", "utx_key", universalTxKey)
		return fmt.Errorf("universal tx with key %s already exists", universalTxKey)
	}

	// use a temporary context to not commit any ballot state change in case of error
	tmpCtx, commit := sdkCtx.CacheContext()

	// Step 2: Record this validator's vote in the per-utx PendingInbounds entry
	// (variant-aware audit trail). Each unique Inbound payload becomes its own
	// variant; multiple variants per utx_key indicate validator divergence.
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}
```
