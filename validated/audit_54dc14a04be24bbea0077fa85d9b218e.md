### Title
Attacker-triggered pending outbound never expires, permanently DoSing `MsgInitiateFundMigration` and freezing TSS key rotation for a chain - (File: x/utss/keeper/msg_initiate_fund_migration.go)

### Summary
The external report's bug class is: a strict "must be zero/empty" precondition gating a sensitive state transition can be permanently violated by an unprivileged attacker who cheaply creates one unit of the disallowed state, and that state is never automatically cleared. In Push Chain, `InitiateFundMigration` enforces exactly this kind of strict precondition — "no pending outbounds for this chain" — but `PendingOutbounds` entries in `x/uexecutor` are documented and coded to persist forever until validator consensus, with **no automatic expiry**. An attacker can cause an outbound to be created, and by design the ballot mechanism does **not** clean up `PendingOutbounds` on expiry, giving an attacker (or even a naturally-slow/never-observed outbound) an indefinite way to block fund migration for that destination chain.

### Finding Description
`InitiateFundMigration` requires the target chain to have zero pending outbounds before an admin can migrate funds from the old TSS vault to the new one: [1](#0-0) 

This mirrors the reported pattern: `getTVLByOwnerOfShares(...) > 0` reverting `_toggleYieldSourceActivation` forever once a griefer deposits 1 share. Here, `HasPendingOutboundsForChain` walks the `PendingOutbounds` collection joined against `UniversalTx` and returns true if any outbound targeting the chain is still `PENDING`: [2](#0-1) 

Outbounds are created automatically whenever a `UniversalTx` gateway call emits a withdraw event — a normal, unprivileged, user-reachable flow (e.g., via `MsgExecutePayload` executing a UEA payload that calls `UniversalTxOutboundEvent` on the gateway) — and are indexed into `PendingOutbounds` before any validator ever votes: [3](#0-2) 

The `PendingOutbounds` entry is documented to be removed **only** when validator consensus (`PASSED`) is reached on the outbound observation ballot, and explicitly **not** on ballot expiry: [4](#0-3) [5](#0-4) 

The only removal code path is the inline `PendingOutbounds.Remove` inside `VoteOutbound`, gated on ballot finalization: [6](#0-5) 

If validators never reach consensus on a given outbound's observation (e.g. it targets a chain nobody actually broadcasts to, uses a malformed/duplicated observation that keeps splitting into new "variants" instead of accumulating votes, or targets a chain where honest validators disagree/never finish observing), the entry stays `PENDING` in `PendingOutbounds` forever — there is no chain-driven expiry. The existing operational workaround is a **manual, one-off upgrade handler** (`purge-expired-outbounds`) that must be run by governance to force-delete expired ballots so validators can re-vote: [7](#0-6) 

The existence of a dedicated upgrade specifically to unstick this exact condition confirms the state is otherwise permanently stuck in production. Because an unprivileged user can trigger outbound creation cheaply (a minimal-value payload call through their own UEA) and there is no automatic cleanup, this satisfies the "1-wei-style griefing" pattern from the source report: a strict zero/empty-state precondition, permanently violatable at negligible attacker cost, blocking a security-critical function.

### Impact Explanation
`InitiateFundMigration` is the only mechanism to move funds from an old TSS-controlled vault to a new one when a TSS key is rotated (`KEYGEN`/`REFRESH`/`QUORUM_CHANGE`). If it can be permanently blocked for a given chain by keeping at least one outbound to that chain stuck `PENDING`, an attacker can prevent the protocol from ever migrating funds off a retiring/compromised TSS key for that chain — a permanent freezing of protocol-controlled funds tied to an old key, and a blocker to remediating a compromised TSS key. This falls within the allowed impact gate ("permanent freezing... of protocol-controlled funds" and "denial of service... reachable without privileged control").

### Likelihood Explanation
Triggering the precondition is trivial and requires no privilege: any user with a deployed UEA (auto-deployable) can submit a `MsgExecutePayload` whose payload causes the gateway to emit a withdraw/outbound event for a low-value amount to a destination chain, which is immediately indexed into `PendingOutbounds` before any vote occurs. Making it *permanently* stuck depends on validators never reaching `PASSED` consensus on that specific outbound's observation — plausible if the outbound targets an edge-case destination (unsupported/misconfigured token pairing, chain the attacker knows validators struggle to observe reliably, or repeated divergent observations that keep spawning new ballot "variants" that never individually hit the vote threshold). The presence of the dedicated `purge-expired-outbounds` upgrade in this exact repository is strong evidence this condition has occurred in practice and required an out-of-band fix.

### Recommendation
- Add an automatic expiry/cleanup path for `PendingOutbounds` entries whose observation ballot has reached `BALLOT_STATUS_EXPIRED`, either by re-arming a fresh ballot automatically (as the upgrade script does manually) or by an operator-triggered (but routine, not one-off-upgrade) admin message.
- Alternatively, change `InitiateFundMigration`'s precondition from "zero pending outbounds" to something that tolerates provably-stuck/expired entries (e.g., ignore entries whose ballot is `EXPIRED` and older than a safety threshold, requiring an explicit admin acknowledgment) rather than an unconditional block.
- Rate-limit or bond outbound-creation-eligible actions (e.g., minimum value/cooldown) so a single dust-cost payload cannot indefinitely occupy a chain's pending-outbound slot.

### Proof of Concept
1. Attacker deploys/auto-deploys a UEA and submits `MsgExecutePayload` whose payload calls the UEA to invoke the gateway's outbound/withdraw path with minimal amount/gas targeting chain `X` (`create_outbound.go` builds and indexes the outbound into `PendingOutbounds` immediately, before any vote).
2. Validators either never reach quorum on this observation (e.g., disagreement, unreliable destination-chain observability, or repeatedly-splitting variants) — the ballot eventually reaches `BALLOT_STATUS_EXPIRED`, but per `pending_outbound.go`/README this does **not** remove the `PendingOutbounds` entry.
3. Admin later attempts `MsgInitiateFundMigration` for chain `X` (e.g., because the current TSS key must be rotated for security reasons). `HasPendingOutboundsForChain` still finds the attacker's stuck entry and `InitiateFundMigration` unconditionally rejects with "chain X still has pending outbounds; wait for them to drain before migration" — permanently, since nothing removes the entry short of a manual chain upgrade (as `purge-expired-outbounds` had to do). [1](#0-0)

### Citations

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L40-47)
```go
	// 5. Verify no pending outbounds for this chain
	hasPending, err := k.uexecutorKeeper.HasPendingOutboundsForChain(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check pending outbounds for chain %s: %w", chain, err)
	}
	if hasPending {
		return 0, fmt.Errorf("chain %s still has pending outbounds; wait for them to drain before migration", chain)
	}
```

**File:** x/uexecutor/keeper/pending_outbound_query.go (L9-34)
```go
// HasPendingOutboundsForChain checks if there are any pending outbounds for a given chain.
// It walks PendingOutbounds and joins against UniversalTx to check destination_chain.
// Returns true on first match. This is O(n) but only called during admin-initiated migration.
func (k Keeper) HasPendingOutboundsForChain(ctx context.Context, chain string) (bool, error) {
	var found bool
	err := k.PendingOutbounds.Walk(ctx, nil, func(outboundId string, entry types.PendingOutboundEntry) (bool, error) {
		utx, exists, err := k.GetUniversalTx(ctx, entry.UniversalTxId)
		if err != nil {
			return true, err
		}
		if !exists {
			return false, nil
		}
		for _, ob := range utx.OutboundTx {
			if ob.DestinationChain == chain && ob.Id == outboundId {
				found = true
				return true, nil // stop walking
			}
		}
		return false, nil
	})
	if err != nil {
		return false, err
	}
	return found, nil
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/uexecutor/README.md (L262-282)
```markdown
### `PendingOutbounds`

- **Created** by chain code at outbound creation in `create_outbound.go` —
  BEFORE any validator vote. The chain knows the outbound exists because it
  generated the destination-chain transaction itself; validators are tasked
  with observing whether/how it landed.
- **Keyed** by deterministic chain-derived `outbound_id`.
- **Variant-aware:** validator votes append `OutboundObservationVariant`s as
  they arrive (`RecordOutboundVote` inside `VoteOutbound`). Multiple variants
  per outbound indicate validator divergence on the destination-chain
  observation (different `success`/`tx_hash`/`error_msg`/`gas_fee_used`).
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```

**File:** x/uexecutor/keeper/pending_outbound.go (L26-31)
```go
// Multiple variants exist for the same outboundId when validators observe
// different destination-chain results (different success/tx_hash/error/gas).
// The variant data is purely an audit trail — PendingOutbounds entries are
// only removed when validators reach consensus (existing inline removal in
// msg_vote_outbound.go on PASSED). Ballot expiry does NOT remove the entry.
// See plan-pending-outbound-cleanup.md for design rationale.
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L121-129)
```go
	// Persist the state inside UniversalTx
	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Remove from pending outbounds index now that status is OBSERVED
	if err := k.PendingOutbounds.Remove(ctx, outboundId); err != nil {
		return fmt.Errorf("failed to remove pending outbound index for %s: %w", outboundId, err)
	}
```

**File:** app/upgrades/purge-expired-outbounds/upgrade.go (L67-94)
```go
// deleteExpiredOutboundBallots iterates all pending outbounds, finds their expired
// ballots, and deletes them. This allows validators to re-vote on the outbounds,
// creating fresh ballots with the current validator set and large expiry.
func deleteExpiredOutboundBallots(ctx sdk.Context, ak *upgrades.AppKeepers) (deleted, skipped, errCount int) {
	logger := ctx.Logger()

	if ak == nil || ak.UExecutorKeeper == nil || ak.UValidatorKeeper == nil {
		logger.Error("purge-expired-outbounds: keeper is nil, skipping")
		return 0, 0, 1
	}

	ek := ak.UExecutorKeeper
	vk := ak.UValidatorKeeper

	// Phase 1: collect all pending outbound entries (avoid mutating during iteration)
	type pendingItem struct {
		outboundId    string
		universalTxId string
	}
	var items []pendingItem

	err := ek.PendingOutbounds.Walk(ctx, nil, func(key string, entry types.PendingOutboundEntry) (bool, error) {
		items = append(items, pendingItem{
			outboundId:    entry.OutboundId,
			universalTxId: entry.UniversalTxId,
		})
		return false, nil
	})
```
