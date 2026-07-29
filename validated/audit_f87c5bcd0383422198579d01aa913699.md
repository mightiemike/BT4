### Title
Unbounded ballot-set scan on every new inbound ballot causes O(n²) gas growth and DoS of universal-execution vote processing - (File: `x/uvalidator/keeper/ballot.go`, `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
The external report's core lesson — "unbounded iteration over a collection whose size an unprivileged actor controls, executed inside a critical, user-reachable state transition" — has a direct analog in Push Chain's ballot machine. `CreateBallot` (`x/uvalidator/keeper/ballot.go:12-68`) unconditionally calls `ExpireBallotsBeforeHeight`, which performs a full `Walk`/iterate over **every** currently-active ballot ID and does a `k.Ballots.Get` for each one, every single time a brand-new ballot is created. In the crosschain inbound-voting path, a new ballot is created any time honest Universal Validators (UVs) observe and vote on a *new* distinct inbound event — and the set of distinct inbound events is driven entirely by ordinary, unprivileged deposit transactions on external chains. Similarly, `x/uexecutor/keeper/ballot_hooks.go:86` (`afterInboundBallotTerminal`) performs a full `Walk` over the entire `PendingInbounds` collection on every single ballot terminal transition to find the matching audit-trail entry.

### Finding Description
`x/uvalidator/keeper/ballot.go`:
```go
func (k Keeper) CreateBallot(...) (types.Ballot, error) {
    ...
    if err := k.ExpireBallotsBeforeHeight(ctx, blockHeight); err != nil { ... }
    ...
}
```
`ExpireBallotsBeforeHeight` (`x/uvalidator/keeper/ballot.go:320-364`) iterates `k.ActiveBallotIDs` in full and, for each entry, performs an additional `k.Ballots.Get` read, on every call:
```go
iter, err := k.ActiveBallotIDs.Iterate(ctx, nil)
...
for ; iter.Valid(); iter.Next() {
    id, _ := iter.Key()
    ballot, _ := k.Ballots.Get(ctx, id)
    if ballot.BlockHeightExpiry <= currentHeight { toExpire = append(toExpire, id) }
}
```
This is invoked from `GetOrCreateBallot` -> `CreateBallot` every time a UV votes on a brand-new ballot ID (`x/uvalidator/keeper/ballot.go:70-89`). In the crosschain inbound flow (`x/uexecutor/keeper/inbound.go`), every logically distinct inbound observation (`utx_key`/ballot variant, keyed by `source_chain:tx_hash:log_index` plus marshalled payload bytes) results in a new ballot ID the first time any UV votes on it. Since the population of "distinct inbound observations" is driven by real deposit transactions submitted by anyone on any registered external chain (`eip155:*`, SVM, etc.), an unprivileged attacker can cheaply generate an arbitrarily large number of small/dust cross-chain deposits before a ballot's `expiryAfterBlocks` window elapses, causing `ActiveBallotIDs` to grow to size *n*. Because `ExpireBallotsBeforeHeight` runs on **every** new ballot creation, the total on-chain work to process *n* new attacker-triggered ballots is O(n²) reads/iterations, executed synchronously inside honest validators' `MsgVoteInbound` transaction processing.

Separately, `afterInboundBallotTerminal` (`x/uexecutor/keeper/ballot_hooks.go:72-97`) walks the entire `PendingInbounds` map on every terminal ballot transition (PASSED/REJECTED/EXPIRED) to locate the owning entry by scanning all variants of all pending entries — another O(n) operation triggered per finalized ballot, compounding the same growth pattern.

### Impact Explanation
This is the direct Cosmos/Push-chain analog of the reported Move bug class: a function invoked as part of routine, attacker-reachable protocol operation (`CreateBallot`, called from the honest-validator inbound voting path — not a privileged admin path) performs unbounded iteration whose cost scales with a quantity (`ActiveBallotIDs` / `PendingInbounds` size) that an unprivileged external depositor fully controls. As the attacker floods the system with many distinct cheap cross-chain deposits within the ballot expiry window, gas/CPU cost of processing each subsequent `MsgVoteInbound` (and thus of finalizing legitimate user inbounds) grows, eventually causing those transactions to fail from gas exhaustion or slow block production for the universal-execution voting path — a denial of service against inbound-tx finalization that is reachable without any privileged or malicious-validator assumption, matching the "denial of service...reachable without privileged control" allowed-impact category.

### Likelihood Explanation
Medium. It requires no colluding validators, no admin, and no compromised keys — only many low-value deposit transactions on any supported external chain, well within reach of a single unprivileged attacker with modest funds (gas on the source chain plus dust amounts). The severity is bounded by Cosmos SDK's per-block gas metering (a single overloaded tx would simply run out of gas and revert rather than corrupt state), which is a key difference from the original Sui hard dynamic-field cap, so the "hard guaranteed permanent failure" characteristic of the original report does not translate 1:1 — the impact here is a gas-cost/DoS degradation rather than a protocol-enforced abort, which lowers confidence that it clears the "material" bar strictly.

### Recommendation
- Decouple ballot expiry sweeping from ballot creation: move `ExpireBallotsBeforeHeight` out of the synchronous `CreateBallot` path and run it as a rate-limited/paginated job (e.g. bounded number of expirations per block in `EndBlocker`, or lazily expire only the specific ballot being looked up).
- Bound and paginate the `afterInboundBallotTerminal` lookup: pass the `utx_key` explicitly to the hook rather than scanning `PendingInbounds` for a matching `BallotId`, eliminating the full-table walk.
- Consider a global or per-source-chain cap / rate limit on simultaneously pending (non-terminal) inbound ballots to bound worst-case per-call work independent of attacker-submitted deposit volume.

### Proof of Concept
Conceptual (no privileged actor required):
1. Attacker submits many distinct low-value deposit transactions (different `tx_hash`/`log_index`) on a registered external chain within one ballot `expiryAfterBlocks` window.
2. Honest UVs observe each deposit and submit `MsgVoteInbound` for each, causing `GetOrCreateBallot` -> `CreateBallot` to fire for each new `utx_key`/ballot variant, growing `ActiveBallotIDs` to size *n*.
3. Each subsequent `CreateBallot` call now performs an O(n) `ExpireBallotsBeforeHeight` scan (with a `Ballots.Get` per entry), and each ballot's terminal transition triggers an O(n) `PendingInbounds.Walk` in `afterInboundBallotTerminal` — total attacker-triggered work is O(n²), degrading gas cost/throughput of all subsequent inbound-vote transactions during the flood. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** x/uexecutor/keeper/ballot_hooks.go (L72-97)
```go
func (h BallotHooks) afterInboundBallotTerminal(
	ctx context.Context,
	ballotID string,
	status uvalidatortypes.BallotStatus,
) error {
	// Ballot IDs are one-way canonical digests (not reversible), so locate
	// the owning audit-trail entry by scanning PendingInbounds for the
	// variant carrying this ballot ID. The pending set is small and
	// transient, and this hook only fires on terminal transitions.
	var (
		utxKey string
		entry  types.PendingInboundEntry
		found  bool
	)
	err := h.k.PendingInbounds.Walk(ctx, nil, func(key string, e types.PendingInboundEntry) (bool, error) {
		for _, v := range e.Variants {
			if v.BallotId == ballotID {
				utxKey, entry, found = key, e, true
				return true, nil
			}
		}
		return false, nil
	})
	if err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/inbound.go (L27-55)
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

	// Find or create the variant for this ballot.
	variantIdx := -1
	for i, v := range entry.Variants {
		if v.BallotId == ballotID {
			variantIdx = i
			break
		}
	}
```
