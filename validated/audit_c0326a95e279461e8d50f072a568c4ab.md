### Title
Unbounded `PendingInbounds.Walk` scan in inbound ballot terminal hook enables gas-exhaustion DoS of inbound finalization - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
`x/uexecutor`'s `BallotHooks.afterInboundBallotTerminal` performs a full linear scan over the entire `PendingInbounds` collection every time any inbound ballot reaches a terminal state (PASSED/REJECTED/EXPIRED). `PendingInbounds` entries are keyed by `utx_key = sha256(source_chain:tx_hash:log_index)` and are created by the very first Universal Validator vote on an inbound observation, staying alive until *all* ballot variants for that entry reach a terminal state [1](#0-0) . Because ballot expiry is deliberately set to `100_000_000` blocks (effectively disabled, "~19 years") [2](#0-1) , entries can only be removed by full quorum finalization, not by timeout. An unprivileged external user can submit a large number of distinct source-chain deposit events (each with a unique `tx_hash`/`log_index`), which get voted onto Push Chain and each create a new `PendingInboundEntry`. As this collection grows, every subsequent inbound-ballot finalization triggers `PendingInbounds.Walk` over the whole map [3](#0-2) , an O(N) (worst case O(N·variants)) state-read operation charged against the finalizing transaction's gas.

### Finding Description
The inbound flow is:
1. A source-chain deposit is observed and voted on by UVs via `MsgVoteInbound` → `Keeper.VoteInbound` → `RecordInboundVote`, which appends/creates a `PendingInboundEntry` keyed by `utx_key` [1](#0-0) .
2. `VoteOnInboundBallot` calls into `x/uvalidator`'s generic ballot machine (`VoteOnBallot` → `CheckIfFinalizingVote` → `MarkBallotFinalized`) [4](#0-3) [5](#0-4) .
3. `MarkBallotFinalized` fires `BallotHooks.AfterBallotTerminal`, which for `INBOUND_TX` ballots calls `afterInboundBallotTerminal` [6](#0-5) .
4. `afterInboundBallotTerminal` cannot directly look up the owning `PendingInboundEntry` by ballot ID (ballot IDs are one-way digests), so it performs an unbounded `Walk` across the **entire** `PendingInbounds` map, and for each entry linearly scans its `Variants` slice, comparing `BallotId` [7](#0-6) . The code comment explicitly (and incorrectly, under adversarial load) assumes "the pending set is small and transient" [8](#0-7) .

`PendingInbounds` size is directly controllable by an unprivileged external attacker: any user can generate arbitrarily many distinct source-chain deposit transactions (even dust amounts) with unique `tx_hash`/`log_index` pairs. Each becomes its own `utx_key` and its own `PendingInboundEntry` once UVs vote on it, and — critically — because `DefaultExpiryAfterBlocks` is set to 100M blocks, none of these entries are pruned by timeout; they persist until quorum finalizes each individually [2](#0-1) . This is analogous to the reported `registeredCustodians` loop: an attacker-influenceable, effectively-unbounded collection is fully scanned inside a critical finalization path (there, `checkpoint`; here, inbound ballot finalization), risking gas exhaustion of the transaction performing that scan.

### Impact Explanation
Once `PendingInbounds` grows large enough (via flooding with many cheap/dust cross-chain deposits from many different source chains/senders — this is fully permissionless and requires no privileged role), every subsequent `MsgVoteInbound` transaction that causes a ballot to reach a terminal state pays for a full `Walk` of the growing map. As the map grows, the gas cost of this walk grows linearly and can eventually exceed the transaction's gas limit (or contribute materially to block gas exhaustion), causing that finalizing UV's vote transaction to fail with out-of-gas. Because this failure is deterministic given the same on-chain state, subsequent honest UVs' finalizing votes on other ballots hit the same growing cost, progressively degrading (and potentially halting) inbound-ballot finalization chain-wide — i.e., new deposits stop reliably converting into `UniversalTx` records, a denial-of-service on universal execution's inbound path reachable purely from ordinary (if abusive) user deposits, without any privileged actor involved. This does not corrupt funds directly, but it can permanently strand in-flight inbound observations in `PendingInbounds` (never reaching PASSED/EXPIRED because the terminal hook itself fails, potentially leaving the ballot on `x/uvalidator`'s side finalized while `x/uexecutor`'s bookkeeping hook errors are only "log-swallowed" per `fireBallotTerminalHook`'s design [9](#0-8) ), degrading audit-trail integrity and blocking the escape-hatch refund flow that depends on `ExpiredInbounds` being correctly populated.

### Likelihood Explanation
Likelihood is moderate-to-low in practice: an attacker must sustain a large volume of genuine source-chain deposit transactions (paying real gas/fees on the external chain) to inflate `PendingInbounds`, and Cosmos SDK KV-store gas costs per read are typically cheap enough that reaching a gas-limit wall requires a very large number of entries. There is no explicit cap on `PendingInbounds` size and no periodic pruning (expiry is effectively disabled), so the collection can only grow monotonically until entries individually finalize — making this a genuine, unmitigated unbounded-growth condition rather than a purely theoretical one, but the cost/attack-effort ratio for the attacker (funding many real cross-chain deposits) is non-trivial compared to a pure on-chain registration abuse.

### Recommendation
- Avoid scanning the entire `PendingInbounds` map to resolve a ballot ID to its owning entry. Maintain a secondary index (e.g., `Map[ballotID]string /* utx_key */`) populated in `RecordInboundVote` alongside variant creation, so `afterInboundBallotTerminal` can do an O(1) lookup instead of `Walk`.
- Alternatively/additionally, bound the size of `PendingInbounds` (e.g., re-enable a bounded expiry, or cap the number of concurrently pending inbound entries with per-source-chain/per-sender rate limiting) so the collection cannot grow unboundedly under attacker-driven deposit flooding.
- Monitor `PendingInbounds` size as an operational metric, consistent with the original report's recommendation for `registeredCustodians`.

### Proof of Concept
1. Attacker submits N distinct dust deposits on a supported source chain, each with a unique `tx_hash`/`log_index` (cheap to generate on the attacker's own account).
2. UVs observe and vote each in via `MsgVoteInbound`; each creates a new `PendingInboundEntry` (`RecordInboundVote`) [1](#0-0) , and since `DefaultExpiryAfterBlocks=100_000_000` [2](#0-1) , none expire.
3. Once N is sufficiently large (bounded only by attacker's willingness to submit deposits and available block gas budget for `Walk`), the next inbound ballot that reaches a terminal state triggers `PendingInbounds.Walk` scanning all N entries plus their variants inside `afterInboundBallotTerminal` [3](#0-2) , consuming gas proportional to N and eventually causing the finalizing UV's `MsgVoteInbound` transaction to run out of gas or contribute to block gas exhaustion, stalling inbound finalization broadly.

Note: I was not able to fully verify the exact Cosmos SDK gas cost per `collections.Walk` KV-store read in this specific fork/version, nor determine the practical N required to exceed a typical block gas limit — this would require running/instrumenting the node, which is outside the scope of this read-only audit.

### Citations

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

**File:** x/uexecutor/types/constants.go (L44-49)
```go
	// Default number of blocks after which ballot expires.
	// Set to 100M (~19 years at 6s blocks) to effectively disable expiry.
	// Ballots should not expire without an escape hatch for stuck pending items.
	// Disabling the expiry temporarily, will most likely enable once ballot pruning is implemented or escape hatch
	DefaultExpiryAfterBlocks = 100_000_000
)
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L56-70)
```go
func (h BallotHooks) AfterBallotTerminal(
	ctx sdk.Context,
	ballotID string,
	ballotType uvalidatortypes.BallotObservationType,
	status uvalidatortypes.BallotStatus,
) error {
	switch ballotType {
	case uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX:
		return h.afterInboundBallotTerminal(ctx, ballotID, status)
	default:
		// OUTBOUND_TX, TSS_KEY, FUND_MIGRATION — not handled here.
		// See doc comment on BallotHooks for rationale on outbound.
		return nil
	}
}
```

**File:** x/uexecutor/keeper/ballot_hooks.go (L76-94)
```go
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
```

**File:** x/uvalidator/keeper/voting.go (L186-196)
```go
	ballot, err = k.AddVoteToBallot(ctx, ballot, voter, voteResult)
	if err != nil {
		return ballot, false, isNew, err
	}

	ballot, isFinalizing, err := k.CheckIfFinalizingVote(ctx, ballot)
	if err != nil {
		return ballot, false, false, err
	}

	return ballot, isFinalizing, isNew, nil
```

**File:** x/uvalidator/keeper/ballot.go (L160-189)
```go
func (k Keeper) MarkBallotFinalized(ctx context.Context, id string, status types.BallotStatus) error {
	if status != types.BallotStatus_BALLOT_STATUS_PASSED && status != types.BallotStatus_BALLOT_STATUS_REJECTED {
		return fmt.Errorf("invalid finalization status: %v", status)
	}

	ballot, err := k.Ballots.Get(ctx, id)
	if err != nil {
		return err
	}

	k.Logger().Debug("marking ballot as finalized",
		"ballot_id", id,
		"final_status", status.String(),
	)

	if err := k.ActiveBallotIDs.Remove(ctx, id); err != nil {
		return err
	}
	if err := k.FinalizedBallotIDs.Set(ctx, id); err != nil {
		return err
	}

	ballot.Status = status
	if err := k.Ballots.Set(ctx, id, ballot); err != nil {
		return err
	}

	k.fireBallotTerminalHook(ctx, ballot.Id, ballot.BallotType, status)
	return nil
}
```

**File:** x/uvalidator/keeper/ballot.go (L191-212)
```go
// fireBallotTerminalHook invokes the registered BallotHooks (if any) and
// log-swallows any error. Terminal transitions must never be blocked by
// secondary-index side-effect failure.
func (k Keeper) fireBallotTerminalHook(
	ctx context.Context,
	ballotID string,
	ballotType types.BallotObservationType,
	status types.BallotStatus,
) {
	if k.ballotHooks == nil {
		return
	}
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := k.ballotHooks.AfterBallotTerminal(sdkCtx, ballotID, ballotType, status); err != nil {
		k.Logger().Warn("ballot terminal hook returned error",
			"ballot_id", ballotID,
			"ballot_type", ballotType.String(),
			"status", status.String(),
			"err", err.Error(),
		)
	}
}
```
