### Title
Inbound-revert and rescue outbound creation bypass the `IsChainOutboundEnabled` lock that gates fund migration safety - (File: x/uexecutor/keeper/build_revert_outbound.go, x/uexecutor/keeper/handle_failed_inbound_validation.go, x/uexecutor/keeper/create_outbound.go)

### Summary
This is the same asymmetric-guard bug class as the Wildcat `FixedTermLoanHooks` finding: one code path enforces an "outbound is locked" invariant while a functionally equivalent path that also creates a pending outbound does not enforce the same guard, letting an unprivileged actor push chain state (and eventually funds) past the boundary the lock was meant to protect.

### Finding Description
`BuildOutboundsFromReceipt` explicitly checks `IsChainOutboundEnabled` before turning a gateway `UniversalTxOutbound` event into a `PendingOutbounds` entry, and rejects the whole receipt if the destination chain has outbound disabled: [1](#0-0) 

This flag exists specifically because `x/utss`'s `InitiateFundMigration` requires outbound to be disabled (and zero pending outbounds) for a chain before funds are swept from the old TSS key to the new one: [2](#0-1) 

However, `buildRevertOutbound` — the function used to create `INBOUND_REVERT` outbounds whenever inbound execution fails — never checks `IsChainOutboundEnabled` for the destination (source) chain before building and returning an outbound: [3](#0-2) 

It is called unconditionally from `handleFailedInboundValidation`, which runs whenever `ValidateForExecution` fails after ballot finalization for a non-CEA inbound, and which any unprivileged user can trigger by crafting a payload/inbound that fails execution: [4](#0-3) 

The resulting `OutboundTx` is attached via `attachOutboundsToUtx`, which writes a fresh `PendingOutbounds` entry unconditionally (no `IsChainOutboundEnabled` check anywhere in this path): [5](#0-4) 

The same asymmetry exists in `ExecuteInboundGas`'s failure branch, which also calls `buildRevertOutbound`/`attachOutboundsToUtx` without any outbound-enabled check: [6](#0-5) 

And `AttachRescueOutboundFromReceipt`, which builds `RESCUE_FUNDS` outbounds for the same source chain, likewise never queries `IsChainOutboundEnabled` before attaching a new outbound.

The invariant these paths violate is exactly the fixed-term/lock invariant from the reported bug: the registry's outbound-enabled flag is meant to be a hard stop on new outbound creation for a chain (used by admins to safely pause a chain or drain it for TSS key migration), but only the "happy path" outbound-creation function (`BuildOutboundsFromReceipt`) enforces it — the revert and rescue outbound-creation functions do not, so an unprivileged user can still force new `PendingOutbounds` entries for a chain that was deliberately outbound-disabled.

### Impact Explanation
This falls under "Registry and accounting path" and "corruption of ... revert destination ... chain config use" in the allowed impact gate: chain config (`IsChainOutboundEnabled`) is supposed to gate all outbound creation for a chain, but it does not gate the revert/rescue creation paths. Concretely:
- Any user can submit an inbound whose payload deliberately fails `ValidateForExecution` (e.g., malformed universal payload, contract revert) after the inbound ballot has passed, forcing `handleFailedInboundValidation` to create a new `INBOUND_REVERT` `PendingOutbounds` entry for that source chain even though the admin explicitly disabled outbound for it.
- `InitiateFundMigration` gates on "no pending outbounds for chain" only at the moment it is called — a user-triggered failed inbound after that check passes can add a new pending outbound for the chain that the operator believed was safely drained, undermining the safety precondition the migration flow relies on.
- Because this pending outbound is picked up by the same TSS pipeline as any other outbound and signed with the then-current key, the chain's outbound-disabled flag — the mechanism operators use to pause/quarantine a chain — is not actually enforced against revert and rescue flows, letting fund egress continue via a code path operators did not account for.

Consistent with the judge's reasoning in the source report, this is not an immediate "present funds at risk" issue in most cases (funds still get signed correctly with the current key), but it breaks a documented and relied-upon safety guarantee (outbound disabled ⇒ no outbound creation) in a way reachable purely by unprivileged users, which is a Medium-class availability/invariant issue rather than a High-severity direct fund theft.

### Likelihood Explanation
High reachability: any unprivileged user can craft an inbound whose execution fails deterministically (e.g. malformed `UniversalPayload`, calling a reverting contract, insufficient gas), which is a normal, permissionless user action, not requiring any privileged or malicious-validator behavior. The only requirement to make the bug materially interesting is that the chain be in the outbound-disabled state (an admin-controlled but externally observable condition, e.g. during a fund migration window), at which point the mismatch becomes exploitable.

### Recommendation
Add the same `IsChainOutboundEnabled` (and, where relevant, chain-existence) check used in `BuildOutboundsFromReceipt` to `buildRevertOutbound` (and its callers `handleFailedInboundValidation`, `ExecuteInboundGas`, `RevertStuckInbound`) and to `AttachRescueOutboundFromReceipt`, so that no new `PendingOutbounds` entry can ever be created for a chain whose outbound flag is disabled. If a revert cannot be created because outbound is disabled, the failure should be persisted on the UTX (e.g. via the existing `RevertError` field) for manual/governance resolution instead of silently creating an outbound.

### Proof of Concept
Not independently executable from static analysis alone; conceptually:
1. Admin disables outbound for chain `eip155:X` via `MsgUpdateChainConfig` and, having confirmed no pending outbounds, calls `MsgInitiateFundMigration` (see check in `x/utss/keeper/msg_initiate_fund_migration.go:31-47`).
2. An unprivileged user submits an inbound `FUNDS_AND_PAYLOAD`/CEA deposit sourced from `eip155:X` whose `UniversalPayload` is crafted to fail `ValidateForExecution` (e.g., calling a reverting/invalid target) after the inbound ballot passes.
3. `handleFailedInboundValidation` runs, calls `buildRevertOutbound` (no outbound-enabled check) and `attachOutboundsToUtx`, creating a new `PendingOutbounds` entry for `eip155:X` — despite outbound being disabled for that chain.
4. This directly contradicts the invariant `InitiateFundMigration` assumed to hold ("no pending outbounds for chain") and the operator's intent that outbound-disabled chains accept no new outbound creation.

This could not be confirmed with a runnable integration test within the scope of this analysis; a Devin session with full repo/test access would be needed to write and run an integration test analogous to `test/integration/uexecutor/chain_enabled_test.go` but targeting the `handleFailedInboundValidation`/`buildRevertOutbound` path instead of `BuildOutboundsFromReceipt`.

### Citations

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

**File:** x/utss/keeper/msg_initiate_fund_migration.go (L31-47)
```go
	// 4. Verify outbound is disabled for this chain
	outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check outbound status for chain %s: %w", chain, err)
	}
	if outboundEnabled {
		return 0, fmt.Errorf("outbound is still enabled for chain %s; disable outbound before initiating migration", chain)
	}

	// 5. Verify no pending outbounds for this chain
	hasPending, err := k.uexecutorKeeper.HasPendingOutboundsForChain(ctx, chain)
	if err != nil {
		return 0, fmt.Errorf("failed to check pending outbounds for chain %s: %w", chain, err)
	}
	if hasPending {
		return 0, fmt.Errorf("chain %s still has pending outbounds; wait for them to drain before migration", chain)
	}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L1-25)
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

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L39-65)
```go
	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			validationErr.Error(),
		); attachErr != nil {
			// Store the revert failure reason on the UTX so it's queryable on-chain.
			// The FAILED PCTx is already recorded above — this adds why the revert wasn't attached.
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
				utx.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				// UpdateUniversalTx only fails on infra issues — return to roll back and retry
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L192-208)
```go
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```
