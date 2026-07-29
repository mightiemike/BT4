### Title
Unbounded per-vote ballot-expiry scan enables gas-growth DoS of universal-validator finalization - (File: x/uvalidator/keeper/ballot.go)

### Summary
The external report describes a Denial-of-Service caused by an unbounded loop (`getTotalVotingPower()`) executed on every vote, whose cost scales with an attacker-inflatable count of entities (veSatin NFTs), eventually exceeding the block gas limit and permanently reverting voting. The Push Chain analog is `ExpireBallotsBeforeHeight` in `x/uvalidator/keeper/ballot.go`, which performs a full linear scan over the `ActiveBallotIDs` collection (fetching every active `Ballot` record) and is exercised opportunistically on ballot creation — the same hot path used by every inbound/outbound observation that universal validators (UVs) vote on.

### Finding Description
`ExpireBallotsBeforeHeight` walks the entire `ActiveBallotIDs` collection, calling `k.Ballots.Get` for every entry to check its expiry height, before a second pass expires the stale ones: [1](#0-0) 

The number of `ActiveBallotIDs` is not bounded by the validator set — a new ballot is created per distinct inbound/outbound payload observed on the universal-execution path. `VoteOnInboundBallot`/`VoteOnOutboundBallot` in `x/uexecutor/keeper/voting.go` compute a per-event `ballotKey` and call `VoteOnBallot`, which internally calls `GetOrCreateBallot`: [2](#0-1) [3](#0-2) 

The integration test `TestCreateBallot_ExpiresOldOnCreate` demonstrates that `CreateBallot` opportunistically triggers expiry-cleanup logic of previously-created ballots on every new ballot creation: [4](#0-3) 

Because a distinct ballot is created for every unique cross-chain observation (source-chain tx hash/log index, or outbound id/hash), an unprivileged attacker who simply originates many low-value cross-chain deposits or withdrawal-triggering transactions on a supported external chain — an entirely ordinary, unprivileged user action — increases the population of active (not-yet-finalized) ballots that honest UVs must vote on. If ballot creation events (i.e., first votes on brand-new observations) continue to arrive faster than the 2/3 quorum can finalize existing ones, `ActiveBallotIDs` grows without bound, and every subsequent new-ballot creation pays an O(N) cost to scan the full active set via `ExpireBallotsBeforeHeight`. This mirrors exactly the veSatin bug class: a loop whose iteration count is driven by attacker-controlled entity growth, executed on a hot, user-reachable path (voting/finalization), which the original report flagged as capable of exceeding per-block gas limits and reverting the operation entirely — here, potentially stalling new-ballot creation and universal-execution voting for the whole protocol as the active set grows.

### Impact Explanation
If the active-ballot count grows large enough, the per-creation scan cost increases linearly, inflating the gas/time cost of processing every new inbound/outbound vote. In the worst case this could exceed practical block gas/time budgets, causing vote-submission transactions to fail or time out, stalling finalization of the universal-execution path (inbound crediting, outbound release) for the whole network — a DoS on core cross-chain functionality, not merely a single actor's funds. This falls within the allowed "denial of service ... not network-level and reachable without privileged control" impact category, since the trigger is ordinary attacker-originated cross-chain traffic, not a compromised validator/relayer.

### Likelihood Explanation
Medium-to-low confidence: while the O(N) scan and its trigger via `CreateBallot` are confirmed in code and tests, I was unable to fully trace (within tool budget) the exact rate at which new distinct ballots can be created per block/attacker cost, nor confirm whether any parameter (e.g. `DefaultExpiryAfterBlocks`, pruning cadence, or a max-active-ballot cap) already bounds `ActiveBallotIDs` size in production. The severity is highly dependent on real-world quorum/finalization throughput versus attacker-affordable deposit spam rate on external chains, which I could not fully quantify from the available index.

### Recommendation
- Bound or amortize the expiry scan: instead of scanning all `ActiveBallotIDs` synchronously on every `CreateBallot` call, maintain a secondary index keyed by expiry height (e.g., `expiry_height -> []ballot_id`) so only ballots actually due for expiry at the current height are touched, avoiding a full-set walk.
- Alternatively, decouple expiry cleanup from the hot vote/create path entirely (e.g., process a small, capped batch of oldest-expiring ballots per block in `BeginBlocker`/`EndBlocker`), bounding worst-case per-call work independent of `ActiveBallotIDs` size.
- Add a hard cap on concurrently active (unfinalized) ballots per observation type, rejecting/queuing new observations once a limit is reached rather than allowing unbounded growth.

### Proof of Concept
1. An external attacker (unprivileged, no protocol permissions) submits a large number of low-value deposit transactions in rapid succession on a supported external chain, each generating a distinct `source_chain:tx_hash:log_index` inbound event.
2. Honest UVs process these deposits per protocol and call `VoteOnInboundBallot` for each new event, which (on the first vote) calls `CreateBallot`, adding a new entry to `ActiveBallotIDs`.
3. As the outstanding (not-yet-2/3-finalized) ballot count grows, every subsequent `CreateBallot` invokes `ExpireBallotsBeforeHeight`, which does a full `Ballots.Get` for every entry in `ActiveBallotIDs`: [5](#0-4) 
4. As `N` (active ballot count) increases, the cost of each new-ballot creation increases linearly, degrading throughput for all subsequent inbound/outbound votes across the protocol — reproducing the same growth-driven DoS pattern as the original veSatin `_vote()` bug, without requiring any privileged actor.

**Caveat:** I could not verify within the available tools whether an existing cap, pagination limit, or separate pruning mechanism already mitigates this in the current codebase; the finding is based on the code paths and test evidence found (`ballot.go` lines 320–364, `ballot_test.go` lines 153–179) and should be validated with a full review of `CreateBallot`/`GetOrCreateBallot` internals and any related module parameters before treating this as confirmed exploitable in production.

### Citations

**File:** x/uvalidator/keeper/ballot.go (L320-364)
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

	// Close iterator explicitly before mutation phase to release the IAVL snapshot
	iter.Close()

	if len(toExpire) > 0 {
		k.Logger().Debug("expiring stale ballots", "count", len(toExpire), "current_height", currentHeight)
	}

	// Phase 2: expire collected ballots (safe — iterator is closed)
	for _, id := range toExpire {
		if err := k.MarkBallotExpired(ctx, id); err != nil {
			return err
		}
	}

	return nil
}
```

**File:** x/uexecutor/keeper/voting.go (L11-26)
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
```

**File:** x/uvalidator/keeper/voting.go (L120-146)
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

**File:** x/uvalidator/keeper/ballot_test.go (L153-179)
```go
func TestCreateBallot_ExpiresOldOnCreate(t *testing.T) {
	f := SetupTest(t)
	require := require.New(t)

	// Create a ballot that expires quickly (expiry = 1 block)
	oldBallot, err := f.k.CreateBallot(f.ctx, "old",
		types.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		[]string{"v1"}, 1, 1)
	require.NoError(err)

	// Manually simulate advancing block height
	f.ctx = f.ctx.WithBlockHeight(oldBallot.BlockHeightCreated + 5)

	// Now create a NEW ballot → should trigger expiry cleanup of the old one
	newBallot, err := f.k.CreateBallot(f.ctx, "new",
		types.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		[]string{"v2"}, 1, 10)
	require.NoError(err)

	// New ballot must be created fine
	require.Equal("new", newBallot.Id)

	// Old ballot should now be expired
	got, err := f.k.GetBallot(f.ctx, "old")
	require.NoError(err)
	require.Equal(types.BallotStatus_BALLOT_STATUS_EXPIRED, got.Status)
}
```
