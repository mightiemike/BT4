### Title
Unbounded `ActiveBallotIDs` growth lets an unprivileged attacker inflate the O(n) full-scan cost of `ExpireBallotsBeforeHeight`, which runs on *every* new ballot creation and can DoS inbound/outbound/TSS/chain-meta finalization for all users - (File: x/uvalidator/keeper/ballot.go)

### Summary
This is the same bug class as the Canto `tickTracking_` finding: an attacker cheaply and repeatedly triggers the creation of new entries in an unbounded on-chain collection, which is then fully iterated on every subsequent, unrelated protocol operation. In Push Chain, the analogous unbounded collection is `x/uvalidator`'s `ActiveBallotIDs` keyset, and the analogous "loop that everyone pays for" is `ExpireBallotsBeforeHeight`, which is invoked unconditionally inside `CreateBallot` — the function that fires every time *any* new crosschain observation (inbound, outbound, chain-meta, TSS event, fund migration) is first voted on by a Universal Validator.

### Finding Description
The generic ballot machine lazily creates a ballot on the first vote for any observation: [1](#0-0) 

`GetOrCreateBallot` calls `CreateBallot`, which unconditionally sweeps the *entire* `ActiveBallotIDs` set before creating the new ballot: [2](#0-1) 

`ExpireBallotsBeforeHeight` performs a full, unbounded iteration over `ActiveBallotIDs`, doing a `Ballots.Get` for every single active ballot to check its expiry height, before any mutation happens: [3](#0-2) 

Because `CreateBallot` is reached from `VoteOnBallot`, which is the single entry point used by every crosschain module (`x/uexecutor` inbound/outbound votes, chain-meta votes, `x/utss` TSS/migration votes): [4](#0-3) 

...every *new, unique* observation submitted honestly by validators forces a full O(len(ActiveBallotIDs)) scan. Crucially, the size of `ActiveBallotIDs` is driven by attacker-controlled input: an external, unprivileged attacker who submits many distinct valid but low-value/dust crosschain deposits (each with a unique `source_chain:tx_hash:log_index`) forces honest Universal Validators to submit one first-vote per distinct inbound, and each first-vote lazily creates a brand-new ballot via `RecordInboundVote`/`VoteInbound`: [5](#0-4) [6](#0-5) 

Nothing bounds how many distinct inbound/outbound/TSS ballots can be pending (`ActiveBallotIDs`) at once, and nothing prunes or caps the set outside of the same `ExpireBallotsBeforeHeight` call that itself does the unbounded work. This mirrors the Canto issue precisely: `tickTracking_` grew unbounded from attacker-triggered tick crosses and was then fully iterated by `accrueConcentratedPositionTimeWeightedLiquidity` on every mint/burn/harvest for *every* liquidity provider — here, `ActiveBallotIDs` grows unbounded from attacker-triggered new observations and is then fully iterated by `ExpireBallotsBeforeHeight` on every new ballot creation for *every* validator vote across all modules.

### Impact Explanation
If `ActiveBallotIDs` is inflated to a large size (thousands+), every subsequent `MsgVoteInbound`, `MsgVoteOutbound`, chain-meta vote, or TSS/migration vote that happens to be the *first* vote on a new observation will pay an O(n) `Ballots.Get` scan before the vote itself is even recorded. This raises the gas/computation cost of routine, honest validator transactions system-wide, and in the worst case (very large backlog) can push these transactions past the block gas limit, causing them to fail or be excluded from blocks. Because ballot creation is the shared choke point for inbound bridging, outbound settlement, chain-meta oracle updates, and TSS/fund-migration coordination, this can stall finalization of crosschain transactions for all users simultaneously — a direct DoS on the universal execution/ballot-finalization pipeline, reachable purely through unprivileged, ordinary deposit submission (no malicious validator, relayer, or admin required).

### Likelihood Explanation
The attack requires only the ability to generate many distinct, individually valid source-chain events (e.g., dust/low-value bridge deposits with unique tx hashes) that pass the existing `IsChainInboundEnabled` check — something any unprivileged external user can do cheaply on most source chains. Honest validators are required to behave normally (vote on what they observe), matching the "honest validators/honest nodes" threat model mandated by the scope. The severity scales with how many distinct pending ballots accumulate before their `expiryAfterBlocks` window elapses; a sustained flood during that window is sufficient to keep `ActiveBallotIDs` large and the per-vote scan expensive.

### Recommendation
- Avoid the unconditional full-set scan inside `CreateBallot`. Move ballot expiry sweeping out of the hot vote path (e.g., run it once per block in `BeginBlock`/`EndBlock` with a bounded batch size) instead of on every new-ballot creation.
- Maintain a secondary, height-ordered index (e.g., keyed/sorted by `BlockHeightExpiry`) so expired ballots can be found and removed without iterating the full `ActiveBallotIDs` set.
- Consider bounding the number of concurrently pending ballots per source chain / per module, or requiring a minimum bridged value / rate-limiting new inbound observations, to remove the attacker's ability to cheaply inflate the active-ballot backlog.
- Ensure `ActiveBallotIDs` iteration is paginated/bounded (a maximum number of entries processed per call) so a single call can never scale linearly with attacker-controlled backlog size.

### Proof of Concept
1. Attacker submits N (e.g., 5,000+) distinct low-value/dust deposits on an enabled source chain, each with a unique tx hash, targeting the Push Chain gateway, within the ballot's `expiryAfterBlocks` window.
2. Honest Universal Validators observe each event and call `VoteInbound` → `RecordInboundVote` → `VoteOnInboundBallot` → `VoteOnBallot` → `GetOrCreateBallot` → `CreateBallot` for each of the N distinct inbounds, since each has a unique `utx_key`/ballot ID.
3. Each of these N `CreateBallot` calls invokes `ExpireBallotsBeforeHeight`, iterating over all currently active ballots (`ActiveBallotIDs`) and calling `Ballots.Get` for each — cost grows linearly with N as the backlog accumulates.
4. Once `ActiveBallotIDs` has grown large, any *new, unrelated* legitimate user's inbound/outbound/chain-meta/TSS vote (submitted by an honest validator) triggers the same expensive O(n) scan before its own vote is processed, increasing gas cost/latency for all subsequent crosschain observations sharing the ballot machine, and can be pushed toward block gas-limit failure with a sufficiently large backlog.

Note: I was unable to fully verify the exact `expiryAfterBlocks`/`votesNeeded` constants used for inbound ballot creation (`x/uexecutor/types/constants.go`) due to the final iteration limit, so the precise attack window (in blocks) and cost-per-dust-deposit could not be quantified numerically; a Devin session with full repository access would be needed to pin down these constants and measure the concrete gas cost per additional active ballot.

### Citations

**File:** x/uvalidator/keeper/ballot.go (L36-39)
```go
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

**File:** x/uvalidator/keeper/voting.go (L120-147)
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

```

**File:** x/uexecutor/keeper/inbound.go (L27-46)
```go
func (k Keeper) RecordInboundVote(
	ctx context.Context,
	inbound types.Inbound,
	voter string,
	ballotID string,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	height := uint64(sdkCtx.BlockHeight())
	utxKey := types.GetInboundUniversalTxKey(inbound)

	entry, err := k.PendingInbounds.Get(ctx, utxKey)
	if err != nil && !errors.Is(err, collections.ErrNotFound) {
		return err
	}
	if errors.Is(err, collections.ErrNotFound) {
		entry = types.PendingInboundEntry{
			UtxKey:          utxKey,
			CreatedAtHeight: height,
		}
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L60-73)
```go
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}
```
