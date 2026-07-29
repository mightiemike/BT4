I have enough evidence now to confirm the analog vulnerability.

### Title
Unbounded `PendingInbounds.Walk` scan on every inbound ballot terminal transition enables gas-exhaustion DoS - (File: `x/uexecutor/keeper/ballot_hooks.go`)

### Summary
`afterInboundBallotTerminal` performs a full, unindexed `Walk` over the entire `PendingInbounds` collection every time *any* inbound ballot reaches a terminal state (PASSED/REJECTED/EXPIRED). `PendingInbounds` grows once per distinct source-chain event (keyed by `utx_key = sha256(source_chain:tx_hash:log_index)`) and is populated purely by ordinary, unprivileged inbound activity — an attacker only needs to originate many distinct low-value source-chain transactions to grow this set arbitrarily, since nothing bounds its size or rate of growth before entries are resolved.

### Finding Description
`h.k.PendingInbounds.Walk(ctx, nil, func(key string, e types.PendingInboundEntry) (bool, error) { ... })` in [1](#0-0)  linearly scans every entry (and every variant inside each entry) in `PendingInbounds` to find the one whose `BallotId` matches the just-terminated ballot, because ballot IDs are one-way digests and not reversible back to the `utx_key`. This function is invoked synchronously, inside the same transaction, from `CheckIfFinalizingVote` → `MarkBallotFinalized` → `fireBallotTerminalHook`, which fires as part of every `MsgVoteInbound` that pushes a ballot over threshold [2](#0-1) .

`PendingInbounds` entries are created by `RecordInboundVote`, keyed by `utx_key` derived purely from `source_chain:tx_hash:log_index` of an inbound gateway event [3](#0-2) . Each new distinct source-chain transaction (however small in value) produces a brand-new `PendingInboundEntry`, and it stays in the collection until every ballot variant attached to it reaches a terminal state. There is no upper bound, expiry pruning independent of ballot resolution, nor any cost accounting proportional to `PendingInbounds` size charged back to whoever caused the entry to be created — the comment in the code itself asserts "the pending set is small and transient" [4](#0-3) , which is an assumption, not an enforced invariant.

Because voting messages (`MsgVoteInbound`) are in the gasless message allowlist [5](#0-4) , the Cosmos-level gas metering that would normally throttle expensive execution via fee payment is bypassed for the very messages that trigger this O(n) scan, and `DeductFeeDecorator`/`MinGasPriceDecorator` skip their normal checks on gasless transactions [6](#0-5) . The EVM block gas meter still applies inside the tx execution, but as `PendingInbounds` grows unboundedly from ordinary unprivileged source-chain activity, each subsequent `Walk` becomes proportionally more expensive, risking `MsgVoteInbound` transactions running out of gas and failing before finalization completes — exactly the failure mode described in the referenced report (transactions failing once an iterated collection grows too large, breaking core functionality).

### Impact Explanation
If `PendingInbounds` grows large enough (via cheap, repeated, unprivileged source-chain transactions that each become a distinct `utx_key`), the per-ballot-finalization `Walk` cost grows linearly with the total number of outstanding pending inbounds. This can cause legitimate `MsgVoteInbound` transactions that push a ballot to threshold to run out of gas or otherwise fail inside `AfterBallotTerminal`, since the hook error is only logged/swallowed for non-fatal cases but an out-of-gas panic during `Walk` is not "non-fatal" — it aborts the enclosing transaction, and since this hook fires from inside `VoteInbound`'s tmpCtx before `commit()`, all of the ballot state and vote-recording changes done in that transaction get rolled back too. This is a denial-of-service against a core universal-execution flow (inbound finalization) reachable purely by ordinary unprivileged users generating many small inbound events, without needing any privileged or malicious-validator behavior.

### Likelihood Explanation
Exploitability requires only that an unprivileged actor issue many distinct low-cost transactions on a supported source chain (e.g., minimal-value transfers to the gateway) so that each becomes a unique `PendingInboundEntry`. No validator collusion, admin action, or protocol bug elsewhere is required — this is purely a function of the number of concurrently-open pending inbounds, which is attacker-influenceable at low cost per entry.

### Recommendation
Avoid the full-collection `Walk` in `afterInboundBallotTerminal`. Maintain a secondary index mapping `ballotID -> utx_key` (populated in `RecordInboundVote` alongside the variant it creates) so the hook can do an O(1) lookup instead of an O(n) scan. Additionally, consider bounding the maximum number of concurrently tracked `PendingInbounds` entries or applying expiry-based pruning independent of ballot finalization, and re-evaluate whether `MsgVoteInbound`'s gasless status should be reconsidered under adversarial growth of `PendingInbounds`.

### Proof of Concept
1. An attacker repeatedly sends many distinct minimal-value transactions on a supported source chain to the gateway contract, each producing a unique `(source_chain, tx_hash, log_index)` triple.
2. Universal Validators observe and vote each one via `MsgVoteInbound`; each becomes its own `PendingInboundEntry` in `x/uexecutor`'s `PendingInbounds` collection (see `RecordInboundVote`, [7](#0-6) ).
3. As the number of outstanding entries grows large, every subsequent ballot finalization (any `MsgVoteInbound` that reaches threshold) triggers `afterInboundBallotTerminal`'s full `Walk` over the now-large `PendingInbounds` set ( [8](#0-7) ), increasing gas cost per finalizing vote proportionally.
4. Eventually, legitimate inbound finalizations begin failing/reverting due to gas exhaustion inside this scan, denying the "vote inbound" functionality for all validators until the pending set is manually resolved.

### Citations

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

**File:** app/txpolicy/gasless.go (L14-26)
```go
func IsGaslessTx(tx sdk.Tx) bool {
	var (
		// GaslessMsgTypes defines the message types that are allowed in gasless transactions
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```

**File:** app/ante/fee.go (L59-64)
```go
	// Check if this is a gasless transaction
	if txpolicy.IsGaslessTx(tx) {
		// Skip fee deduction for Gasless messages
		ctx.Logger().Debug("deduct fee decorator: gasless tx detected, skipping fee deduction")
		return next(ctx, tx, simulate)
	}
```
