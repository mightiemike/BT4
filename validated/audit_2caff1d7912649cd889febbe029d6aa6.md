### Title
Inbound-revert outbounds bypass the `IsChainOutboundEnabled` gate that all other outbound-creation paths enforce, permanently stalling user refunds - ([File: x/uexecutor/keeper/build_revert_outbound.go])

### Summary
Push Chain enforces a per-chain outbound pause (`ChainConfig.Enabled.IsOutboundEnabled`) at exactly one on-chain choke point — `BuildOutboundsFromReceipt` — but the INBOUND_REVERT outbound-creation path (`buildRevertOutbound`, called from `execute_inbound_funds.go`, `execute_inbound_gas.go`, `execute_inbound_funds_and_payload.go`, `handle_failed_inbound_validation.go`, and `admin_revert.go`) never checks that flag before writing a `PENDING` outbound to state. This is the same class of bug as the external report: one "safety/exit" flow (revert-to-user, analogous to "repay") is not gated the same way the "normal" flow (analogous to "liquidation"/withdraw) is, and the two flags governing inbound vs. outbound for the same chain can diverge.

### Finding Description
`x/uregistry.ChainConfig.Enabled` carries two independent booleans, `IsInboundEnabled` and `IsOutboundEnabled`, and nothing in the module prevents the admin-curated combination `IsInboundEnabled=true, IsOutboundEnabled=false` for the same chain [1](#0-0) . This is exactly the class of divergent-pause state the bug report warns about (repay open, liquidation open — here inbound open, outbound closed for the *same* chain).

When a normal on-chain outbound is created from an EVM receipt, `BuildOutboundsFromReceipt` explicitly checks `IsChainOutboundEnabled` and refuses to build the outbound (`"outbound is disabled for chain %s"`) if the destination chain has outbound paused: [2](#0-1) 

However, the parallel "protective/exit" path — the INBOUND_REVERT outbound that returns a user's bridged funds to the source chain when Push-Chain-side execution fails — is built by `buildRevertOutbound`, which performs **no** `IsChainOutboundEnabled` check at all before constructing the `OutboundTx` and handing it to `attachOutboundsToUtx`: [3](#0-2) 

`attachOutboundsToUtx` unconditionally appends the outbound to the UTX, writes a `PendingOutbounds` entry (with a computed `SigningDeadline`), and emits an `OutboundCreatedEvent` for UV/TSS pickup — again with no outbound-enabled check: [4](#0-3) 

This path is reachable by an ordinary, unprivileged user: any inbound deposit whose Push-Chain-side execution fails (`ExecuteInboundFunds`, `ExecuteInboundGas`, `ExecuteInboundFundsAndPayload`, or `handleFailedInboundValidation`) triggers `buildRevertOutbound` for the *inbound's source chain* — the chain the outbound will need to be signed and broadcast back to.

The only place the disabled-outbound state is actually enforced against a revert outbound is off-chain, in the Universal Validator's TSS session manager, which checks `chains.IsChainOutboundEnabled(chainID)` before agreeing to sign *any* `SIGN_OUTBOUND` event (including revert-type outbounds, since the destination chain field is the same for both): [5](#0-4) 

Because the on-chain module never gates revert-outbound *creation* on the same flag, and no compensating recovery path exists once an outbound lands in this dead state (`checkExpiredSessions` only rolls the event back to `CONFIRMED` for retry, it never routes around the outbound-disabled condition, and there is no `RevertStuckOutbound`/rescue admin message analogous to `RevertStuckInbound` in this codebase snapshot): [6](#0-5) 
the revert outbound is created on-chain, indexed in `PendingOutbounds` forever, and can never be honestly signed — because the honest UVs will always refuse per the check above. The affected user's bridged funds (already minted/consumed on the Push-Chain side and now supposed to be returned) become permanently un-refundable through the normal protocol flow as long as outbound stays disabled for that chain, with no on-chain signal, error, or alternate remedy surfaced at creation time.

### Impact Explanation
This matches the "permanent freezing of user or protocol-controlled funds" and "corruption of ... canonical UniversalTx state" categories in scope. A user's `OutboundTx` sits in `PENDING` status inside a canonical, append-only `UniversalTx` record and in the `PendingOutbounds` index indefinitely, with UVs unable to ever honestly sign it while outbound remains disabled for that chain — even though the on-chain module gave every appearance (via `PENDING` status + `PendingOutbounds` index + emitted event) that the refund was in flight. There is no automatic abort/rescue trigger tied to the "outbound disabled" condition, unlike the existing `RevertStuckInbound`/ballot-expiry admin path for stuck inbounds. This is a genuine asymmetry between two pause-flag-consuming code paths for the identical destination chain that a bug-bounty scan would flag as directly matching the "repay paused but liquidation enabled" bug class: the exit/repay-equivalent flow (fund revert) is not gated the same way the deposit and forward-outbound flows are.

### Likelihood Explanation
Reaching the divergent state does require the admin to set `IsInboundEnabled=true`/`IsOutboundEnabled=false` for a chain, which is an intended, legitimate admin configuration (e.g., pausing outbound broadcasting for a chain suspected of a compromised gateway or under maintenance, while still allowing inbound deposits). Given that configuration exists (which the module itself allows without cross-validation), the trigger itself — any inbound whose Push-Chain execution fails — is fully unprivileged and can be caused by an ordinary user (or occurs naturally from execution errors), making the funds-freezing outcome reachable without any privileged attacker action beyond the admin's independent, legitimate flag choice.

### Recommendation
Add the same `IsChainOutboundEnabled` check (or an equivalent gate) inside `buildRevertOutbound` / `attachOutboundsToUtx` before creating and indexing an INBOUND_REVERT outbound, so the module never creates a revert outbound it cannot honestly get signed. If outbound is disabled at revert-build time, either (a) hold the failed-execution state and retry the revert construction later (analogous to periodic re-check), or (b) explicitly surface a distinct "revert blocked, awaiting outbound re-enable" status on the UTX and provide an admin/queryable recovery path once the chain's outbound flag is turned back on. More generally, enforce mutual-consistency validation on `MsgAddChainConfig`/`MsgUpdateChainConfig` (or at minimum documentation-level warnings) about the risk of the `IsInboundEnabled=true, IsOutboundEnabled=false` combination, and add an escape-hatch (comparable to `MsgRevertStuckInbound`) for outbounds stuck solely due to chain-level outbound pause.

### Proof of Concept
1. Admin sets `ChainConfig` for chain `eip155:X` with `Enabled.IsInboundEnabled=true`, `Enabled.IsOutboundEnabled=false` (a legitimate, permitted admin action per `x/uregistry`) — no code path rejects this combination.
2. An unprivileged user's inbound (deposit) event on `eip155:X` is voted and finalized normally (inbound checks pass since inbound is enabled).
3. Push-Chain-side execution of that inbound fails for any ordinary reason (e.g., `depositPRC20` reverts) inside `ExecuteInboundFunds`.
4. `handleFailedInboundValidation`/`ExecuteInboundFunds` calls `buildRevertOutbound(sdkCtx, inbound)` — no `IsChainOutboundEnabled` check occurs — and `attachOutboundsToUtx` writes a `PENDING` `OutboundTx` (destination = `eip155:X`) into the UTX and `PendingOutbounds`, emitting `OutboundCreatedEvent`.
5. Universal Validators receive the `SIGN_OUTBOUND` event, and `sessionmanager.verifyOutboundSigningRequest` rejects signing with `"outbound disabled for chain eip155:X, refusing to sign"` for every UV, every retry cycle.
6. The outbound remains `PENDING` in `PendingOutbounds`/`UniversalTx.OutboundTx` indefinitely; the user's bridged funds are never returned, and no on-chain path exists to reconcile or force resolution while the admin's outbound-disabled configuration for that chain persists.

Because the review here relies on integration-test evidence and static code paths rather than a live cluster run, I was not able to fully verify whether a subsequent chain-config change (re-enabling outbound) would automatically un-stick the queued revert outbound and allow normal signing to proceed — that would require running the retry/session-expiry loop end to end.

### Citations

**File:** x/uregistry/README.md (L31-33)
```markdown
  uint64                gas_oracle_fetch_interval = 8;
  ChainEnabled          enabled                  = 9;  // is_inbound_enabled, is_outbound_enabled
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-57)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}
```

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L1-26)
```go
package keeper

import (
	sdk "github.com/cosmos/cosmos-sdk/types"
	"github.com/pushchain/push-chain-node/x/uexecutor/types"
)

// buildRevertOutbound creates an INBOUND_REVERT outbound with gas fields populated
// from the UniversalCore contract via getOutboundTxGasAndFees.
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}

```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L874-895)
```go
			var (
				updates map[string]any
				logMsg  string
			)
			if signed != nil {
				updates = map[string]any{"status": store.StatusSigned}
				logMsg = "expired session removed; signing_data present, restored to SIGNED"
			} else {
				newBlockHeight := currentBlock + blockDelay
				updates = map[string]any{
					"status":       store.StatusConfirmed,
					"block_height": newBlockHeight,
				}
				logMsg = "expired session removed, event marked as pending for retry"
			}
			if err := sm.eventStore.Update(eventID, updates); err != nil {
				sm.logger.Warn().Err(err).Str("event_id", eventID).
					Msg("failed to update expired session event")
			} else {
				sm.logger.Info().Str("event_id", eventID).Msg(logMsg)
			}
		}
```

**File:** universalClient/tss/sessionmanager/sessionmanager.go (L920-923)
```go
	// Reject signing if outbound is disabled for the destination chain
	if sm.chains != nil && !sm.chains.IsChainOutboundEnabled(chainID) {
		return fmt.Errorf("outbound disabled for chain %s, refusing to sign", chainID)
	}
```
