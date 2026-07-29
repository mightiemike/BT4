### Title
Unbounded `ActiveBallotIDs` iteration in `CreateBallot`/`ExpireBallotsBeforeHeight` causes Out-of-Gas DoS on new inbound/outbound ballot creation - (File: `x/uvalidator/keeper/ballot.go`)

### Summary
Every new ballot creation in `x/uvalidator` (triggered on every first vote for a new inbound or outbound observation) synchronously walks the *entire* `ActiveBallotIDs` collection to find stale ballots to expire, exactly like the reported `massUpdatePools`/`add(..., withUpdate=true)` pattern. Because ballot creation itself is what triggers this full scan, and new distinct ballots are created for every distinct source-chain event a validator observes, an attacker who causes many distinct inbound (or outbound) observations to be recorded without them reaching quorum/expiry fast enough can grow `ActiveBallotIDs` without bound. Once the set is large enough, the O(n) walk performed inside `CreateBallot` exceeds the block gas limit, and **all subsequent `VoteInbound`/`VoteOutbound` calls that need to create a new ballot fail**, halting processing of new cross-chain deposits/outbounds chain-wide.

### Finding Description
`CreateBallot` unconditionally calls `ExpireBallotsBeforeHeight` before creating the requested ballot: [1](#0-0) 

`ExpireBallotsBeforeHeight` iterates the full `ActiveBallotIDs` collection with no pagination or cap, doing a `Ballots.Get` per active ballot: [2](#0-1) 

This is invoked on every new ballot request via `GetOrCreateBallot`: [3](#0-2) 

Ballots for inbound events are keyed by `hex(marshal(Inbound))` (a distinct ballot per distinct observed payload), and a brand-new ballot is created the moment the *first* validator votes on a never-before-seen inbound event, via `VoteOnInboundBallot` inside `VoteInbound`: [4](#0-3) 

Honest validators generate these votes automatically in response to real events observed on the source chain — an unprivileged external attacker does not need to be a validator; they only need to cause many distinct low-value/dust cross-chain deposit (or outbound-observable) events on a supported external chain, each of which becomes a new, unique ballot ID. As long as validators keep voting on these newly observed events faster than the existing ones expire (`BlockHeightExpiry`), `ActiveBallotIDs` grows unbounded, and each subsequent `CreateBallot` call becomes more expensive because it re-scans the whole active set from scratch.

This directly mirrors the reported `massUpdatePools` bug class: an attacker-controllable, ever-growing "registered pool" list (`ActiveBallotIDs`) is fully iterated inside the very same function (`add`/`CreateBallot`) that is needed to admit new entries, so growth of the list eventually makes admission of new entries (and by extension, all new inbound/outbound votes) fail with an out-of-gas error.

### Impact Explanation
Once the active-ballot set is large enough that `ExpireBallotsBeforeHeight`'s full walk exceeds the per-transaction/block gas budget, `CreateBallot` (and hence `GetOrCreateBallot`) fails for **every** new inbound/outbound event across all chains, not just the attacker's own event. This blocks:
- Recording new validator votes for legitimate user cross-chain deposits (`VoteInbound`).
- Recording new validator votes for outbound observations (`VoteOutbound`).
- Any other ballot type that goes through `CreateBallot` (TSS key ballots, fund-migration ballots), since they share the same `ActiveBallotIDs` collection and the same code path.

This is a chain-wide, non-network-level denial of service reachable by an ordinary unprivileged user simply generating many distinct cross-chain events, satisfying the "DoS...reachable without privileged control" allowed-impact category. It does not require malicious validators, TSS participants, or admin/governance abuse — only ordinary (if numerous) external-chain transactions that validators will faithfully observe and vote on.

### Likelihood Explanation
Likelihood depends on: (1) the cost to the attacker of generating many distinct observable events on a supported external chain (cheap on low-fee chains), (2) the ballot expiry window (`expiryAfterBlocks`) relative to the rate at which new distinct ballots can be created, and (3) the actual gas cost of the `Ballots.Get` + iterator step per active entry. Because ballot cleanup only happens as a side effect of creating a *new* ballot (self-referential design — you must create a ballot to clean up old ones), there is no independent, gas-metered garbage collection (e.g. a paginated `BeginBlocker` sweep) to bound the set size. This makes sustained growth plausible if the attacker's event-generation rate exceeds validators' vote/finalization/expiry rate, though the exact number of ballots needed to hit the gas ceiling would require load-testing to confirm precisely.

### Recommendation
- Decouple ballot expiry from ballot creation: move `ExpireBallotsBeforeHeight` (or an equivalent) into a `BeginBlocker`/`EndBlocker` job that processes a bounded, paginated batch of active ballots per block (similar to how `query_server.go` uses `query.CollectionPaginate` for read paths).
- Alternatively, cap the number of active ballots processed per `CreateBallot` call and carry over the iterator/cursor across calls, so no single call performs unbounded work.
- Enforce a hard ceiling on `ActiveBallotIDs` size (reject/backpressure new ballot creation gracefully rather than failing via out-of-gas) and/or shrink `expiryAfterBlocks` defaults to reduce worst-case set size.
- Add metrics/alerts on `ActiveBallotIDs` cardinality so operators can detect abnormal growth before it becomes exploitable.

### Proof of Concept
1. An attacker submits many small/dust transactions on a supported external source chain (e.g., `eip155:11155111`), each with a unique `tx_hash`/`log_index`, so each is a distinct `Inbound` per `GetInboundBallotKey`.
2. Honest validators observe each event and submit `MsgVoteInbound`, which calls `VoteInbound` → `VoteOnInboundBallot` → `GetOrCreateBallot` → `CreateBallot`, adding a new entry to `ActiveBallotIDs` for each.
3. As long as ballots aren't expiring/finalizing faster than they're created (e.g., attacker paces events just under the expiry window, or throughput of legitimate votes lags), `ActiveBallotIDs` grows without bound across blocks.
4. Once `ActiveBallotIDs` is large enough, the next `CreateBallot` call's `ExpireBallotsBeforeHeight` walk (iterating and `Get`-ing every active ballot) exceeds the available gas, causing the enclosing `MsgVoteInbound`/`MsgVoteOutbound` transaction to fail for all validators and all chains, since they all funnel through the same `Ballots`/`ActiveBallotIDs` collections. [5](#0-4) [6](#0-5)

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
