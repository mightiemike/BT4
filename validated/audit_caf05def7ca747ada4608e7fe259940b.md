### Title
Unbounded `PendingInbounds` walk in `afterInboundBallotTerminal` allows attacker-inflated map to make inbound-ballot processing gas cost grow with total pending inbounds - ([File: x/uexecutor/keeper/ballot_hooks.go])

### Summary
`x/uexecutor`'s `BallotHooks.afterInboundBallotTerminal` locates the `PendingInbounds` entry owning a terminating ballot by doing a full linear `Walk` over the entire `PendingInbounds` collection on **every** inbound-ballot terminal transition (PASSED/REJECTED/EXPIRED), rather than looking it up by key. [1](#0-0) 
This mirrors the HAL-07 pattern: an unbounded, attacker-growable collection (`users` array in SHToken / `PendingInbounds` map here) that is iterated in full on a hot path, so the cost of routine operations grows with the size of the attacker-controlled set.

### Finding Description
`PendingInbounds` entries are created the moment the *first* validator vote for a given source-chain event arrives, keyed by `sha256(source_chain:tx_hash:log_index)`, via `RecordInboundVote`. [2](#0-1) 
This entry is created from a real, honestly-observed source-chain event, but the event itself can be triggered arbitrarily cheaply by an unprivileged attacker (e.g., a dust-value deposit on a cheap/low-fee source chain that the `x/uregistry` chain config allows for inbound). Nothing in `RecordInboundVote` bounds the number of concurrently-pending inbound entries.

Each such entry is removed only when its ballot(s) reach a terminal state, which is driven by `BallotHooks.AfterBallotTerminal` → `afterInboundBallotTerminal`. [3](#0-2) 
Because ballot IDs are one-way digests that cannot be reverse-mapped to the `utx_key`, the hook resolves the owning entry by walking **every** key in `PendingInbounds` and scanning each entry's `Variants` slice for a matching `BallotId`: [1](#0-0) 
This walk runs on every single terminal transition of every inbound ballot — i.e., once per distinct inbound event processed by `MsgVoteInbound` (via `VoteOnInboundBallot` → `CheckIfFinalizingVote` → uvalidator's ballot-terminal hook dispatch), as shown in the vote-inbound flow. [4](#0-3) 

If an attacker keeps `N` inbound events simultaneously pending (by submitting many cheap/dust cross-chain deposits in a burst, faster than validators can finalize them), then finalizing each subsequent inbound costs `O(N)` due to the full walk, and processing all `N` pending inbounds costs `O(N²)` total. As `N` grows, the per-transaction gas cost of `MsgVoteInbound` (a **gasless**, whitelisted message type per `app/txpolicy/gasless.go`) rises toward the block gas limit, which can stall or fail the finalization step for legitimate inbounds — the exact "array grows unbounded → iteration cost prohibitive → core function DoS" pattern from the HAL-07 report, just with a KV-store `Walk` in place of a Solidity array.

The code's own comment acknowledges this is an assumption, not a guarantee: "The pending set is small and transient" — but there is no enforcement of that assumption (no cap on `PendingInbounds` size, no rate limit on distinct-event admission). [5](#0-4) 

### Impact Explanation
This falls under the allowed "denial of service... not network-level and reachable without privileged control" impact. An unprivileged external user can, purely through ordinary cross-chain deposit submission (the standard inbound path, no admin/validator/relayer privilege required), inflate `PendingInbounds` to a large size and thereby degrade or stall the inbound-vote-finalization pipeline for all users, since honest validators must pay increasing gas to process `MsgVoteInbound` for unrelated, legitimate inbounds while the attacker's flood of entries remains pending. In the worst case this can push per-tx gas above the effective limit, causing `MsgVoteInbound` execution to fail (out-of-gas) for the block proposer, which can repeatedly reject/rebroadcast legitimate votes and delay UTX creation for genuine users' funds.

### Likelihood Explanation
Likelihood is moderate-to-high: the entry point (`MsgVoteInbound`/underlying source-chain deposit) is fully permissionless and gasless for the validator side, and creating many low-value inbound events across a supported low-fee source chain is inexpensive for an attacker. The severity is bounded by how quickly validators finalize ballots (2/3 threshold) and by `x/uregistry`'s inbound-enabled chain set, but the design has no explicit cap that prevents the pending set from growing large during a burst.

### Recommendation
Avoid the linear scan for locating the `PendingInbounds` entry that owns a given ballot ID. Maintain a secondary index (e.g., `collections.Map[ballotID, utxKey]`) populated in `RecordInboundVote` alongside variant creation, and have `afterInboundBallotTerminal` do a direct key lookup instead of a full `Walk`. Additionally, consider capping the number of concurrently-pending distinct inbound entries (or applying inbound-value/rate thresholds) so that even before the index fix lands, the attacker-controllable set size cannot grow unbounded.

### Proof of Concept
Conceptual (this could not be executed in ask-only mode, but the mechanics trace directly from the code read):
1. An attacker submits `N` (e.g., several thousand) distinct dust-value deposits on an inbound-enabled external chain, each producing a unique `(source_chain, tx_hash, log_index)`.
2. Validators observe and vote via `MsgVoteInbound` for each; `RecordInboundVote` creates `N` separate `PendingInboundEntry` records in `PendingInbounds`. [2](#0-1) 
3. As 2/3+ of validators vote on each event, `VoteOnInboundBallot` finalizes each ballot, invoking `AfterBallotTerminal` → `afterInboundBallotTerminal`, which performs a full `Walk` over the (still largely populated) `PendingInbounds` map to find the matching `BallotId`. [1](#0-0) 
4. Measuring gas consumption of `MsgVoteInbound` as `N` grows would show consumption scaling with `len(PendingInbounds)`, demonstrating the same "iteration cost grows unbounded with attacker-inflated set" pattern as HAL-07's `deleteUserFromArray`.

### Citations

**File:** x/uexecutor/keeper/ballot_hooks.go (L36-70)
```go
// AfterBallotTerminal is invoked by x/uvalidator when a ballot reaches a
// terminal state. For INBOUND_TX ballots this:
//
//  1. Marks the matching variant in the PendingInbounds entry with the
//     terminal status that was reached.
//  2. If ANY variant is still PENDING, persists the updated entry and
//     returns — the entry continues to wait on the remaining ballot(s).
//  3. If ALL variants are now terminal:
//     a. Removes the entry from PendingInbounds.
//     b. If any variant ended PASSED, the existing post-finalization path
//        in VoteInbound has already produced a UniversalTx — nothing more
//        to do.
//     c. If ALL variants ended EXPIRED/REJECTED (no UTX was ever created),
//        copies the entry into ExpiredInbounds preserving the full
//        per-variant audit trail for the future escape-hatch refund flow.
//
// Hook implementations are required to be idempotent and must not block
// the terminal transition by returning errors for non-fatal conditions.
// Decode failures and "entry already cleared" cases are warning-logged
// and swallowed.
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

**File:** x/uexecutor/keeper/ballot_hooks.go (L77-94)
```go
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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L60-84)
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

	commit()

	// Voting not finalized yet
	if !isFinalized {
		k.Logger().Debug("vote inbound recorded, ballot not yet finalized",
			"validator", universalValidator.String(),
			"utx_key", universalTxKey,
		)
		return nil
	}
```
