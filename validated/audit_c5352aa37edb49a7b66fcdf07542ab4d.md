### Title
Attacker-inflatable `PendingInbounds` map forces O(n) full-map scan on every inbound ballot finalization - ([File: x/uexecutor/keeper/ballot_hooks.go])

### Summary
The external report's bug class is: an unprivileged actor can cheaply create many low/zero-value entries in an array that a victim (claimer/protocol) must later iterate over in full, paying a fixed per-entry cost, turning normal operation into a gas/DoS problem. The Push Chain analog is the `PendingInbounds` collection map in `x/uexecutor`, which is grown by ordinary user-triggered cross-chain events and is fully scanned (`Walk`) on every single inbound ballot finalization inside `BallotHooks.afterInboundBallotTerminal`.

### Finding Description
Every observed source-chain inbound (even a dust-value transfer) creates or updates an entry in `PendingInbounds`, keyed by `utx_key = sha256(source_chain:tx_hash:log_index)`, via `RecordInboundVote` [1](#0-0) . There is no minimum-amount gate before a UV vote is recorded and a `PendingInboundEntry`/ballot is created for it — `MsgVoteInbound.ValidateBasic` only checks the signer and delegates to `Inbound.ValidateBasic()` (no amount floor) [2](#0-1) .

Each distinct inbound event drives its own ballot through `VoteOnInboundBallot`/`VoteOnBallot`, and when a ballot reaches a terminal state, `MarkBallotFinalized` unconditionally fires the registered `BallotHooks.AfterBallotTerminal` callback [3](#0-2) . For `INBOUND_TX` ballots this dispatches to `afterInboundBallotTerminal`, which — because ballot IDs are one-way digests and not directly invertible to a `utx_key` — locates the owning `PendingInboundEntry` by performing a **full `Walk` over the entire `PendingInbounds` collection**, scanning every entry's `Variants` slice looking for a matching `BallotId`: [4](#0-3) 

The code comment explicitly assumes "the pending set is small and transient" — an assumption that an attacker directly controls by generating many distinct low-value source-chain transactions. Because this hook fires **once per finalized ballot, for every single inbound event**, the aggregate cost of processing `n` attacker-created inbound events is `O(n)` scans each costing `O(current-map-size)`, i.e. up to `O(n²)` total work — done unconditionally by every node processing `MsgVoteInbound` transactions, which are gasless (`/uexecutor.v1.MsgVoteInbound` is in the gasless allowlist) [5](#0-4) .

This is structurally identical to the Dutch Auction bug: an attacker manufactures many cheap/low-value entries in a structure (`claimerToBuyers` array / `PendingInbounds` map) that some other actor (claimer / honest validating nodes) must iterate in full, at fixed per-entry (or per-scan) cost, on a hot path they don't control the size of.

### Impact Explanation
Because `MsgVoteInbound` is gasless and requires no minimum transferred value, an attacker who can originate cheap dust transactions on any enabled source chain (a normal, unprivileged user action — no admin/UV/TSS key required) can force honest Universal Validators to observe and vote on an unbounded number of distinct inbound events. Each such vote, once it finalizes its own (single-voter or low-threshold) ballot, triggers a full linear scan of `PendingInbounds` by every node processing that vote. As the map grows, per-event processing cost grows linearly, and total cost across `n` attacker-triggered events grows quadratically — a non-network-level denial-of-service / resource-exhaustion vector reachable purely through ordinary attacker-controlled deposits, without any privileged validator, admin, or TSS compromise. This can degrade block processing time for all nodes and, in the worst case, make it costly or impractical to keep up with vote processing, which is exactly the impact class flagged in scope ("denial of service only when it is not network-level and is reachable without privileged control").

### Likelihood Explanation
Likelihood is moderate-to-high in principle: triggering it only requires the attacker to originate many low-value/dust transactions on a supported source chain and wait for honest UVs to observe and vote them (which they are expected to do for all valid inbound events, per the "always create a UniversalTx" design philosophy stated in `VoteInbound`'s own doc comment) [6](#0-5) . The main uncertainty (which I could not fully verify from available code/index) is the actual vote-threshold sizing and how quickly ballots for distinct dust transactions individually finalize versus sit pending, and whether any chain-level rate limiting or per-chain inbound throttling exists elsewhere in `x/uregistry` that would reduce the practical rate of injectable entries. Those would need to be checked in a live/full codebase session before assigning a final severity, since the index used here may not include every relevant guard.

### Recommendation
- Enforce a minimum transferable amount (or a minimum-value floor per source chain, configurable via `x/uregistry` chain config) before an inbound vote is even recorded/ballot created, mirroring the audit's original remediation ("set a minimum purchase amount").
- Avoid the full `PendingInbounds.Walk` on every ballot termination: store a reverse index from `ballot_id -> utx_key` (or embed `utx_key` in data the hook already receives) so `afterInboundBallotTerminal` can do an O(1) lookup instead of an O(n) scan.
- Consider bounding the maximum number of concurrently pending distinct inbound entries per source chain, with older/lowest-value entries evicted or rate-limited, so the cost of processing this hot path cannot be inflated by unprivileged actors.

### Proof of Concept
1. Attacker sends `n` distinct dust-value transfers (e.g., 1 unit of the bridged asset each) from an inbound-enabled source chain to the Push Chain gateway, each with a unique `tx_hash`/`log_index`.
2. Honest Universal Validators observe each event and submit `MsgVoteInbound` for each (gasless, no minimum-amount check enforced in `ValidateBasic`).
3. Each `RecordInboundVote` call creates a brand-new `PendingInboundEntry` keyed by a unique `utx_key`, growing `PendingInbounds` to size `n` [7](#0-6) .
4. As each of the `n` ballots individually finalizes, `MarkBallotFinalized` fires `afterInboundBallotTerminal`, and each invocation performs a full `Walk` over the then-current `PendingInbounds` map (size growing toward `n`) [8](#0-7) .
5. Total node-side work across the attack is `O(n²)`, driven entirely by unprivileged, low-cost attacker transactions, with no minimum-value or rate-limiting guard preventing it.

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

**File:** x/uexecutor/types/msg_vote_inbound.go (L52-59)
```go
// ValidateBasic does a sanity check on the provided data.
func (msg *MsgVoteInbound) ValidateBasic() error {
	// validate signer
	if _, err := sdk.AccAddressFromBech32(msg.Signer); err != nil {
		return errors.Wrap(err, "invalid signer address")
	}

	return msg.Inbound.ValidateBasic()
```

**File:** x/uvalidator/keeper/ballot.go (L160-188)
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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L13-17)
```go
// VoteInbound is for uvalidators for voting on synthetic asset inbound bridging.
// After ballot finalization, a UniversalTx is always created on-chain regardless of
// whether the inbound passes execution validation. This ensures the user can always
// query what happened to their cross-chain tx instead of having funds silently stuck
// in the gateway contract.
```
