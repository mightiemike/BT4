### Title
Payload-triggered outbound silently dropped when destination chain outbound is disabled mid-flight, permanently stranding already-deposited/burned funds — (File: `x/uexecutor/keeper/create_outbound.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
`Guard::updateHookStatus`-style disruption maps to Push Chain's `uregistry` chain `Enabled.IsOutboundEnabled` flag. When a payload executed as part of inbound finalization emits a `UniversalTxOutbound` event (i.e., the UEA's payload already burned/locked PRC20 and asked for a cross-chain release), `BuildOutboundsFromReceipt` rejects the outbound with `"outbound is disabled for chain %s"` if an admin has since disabled outbound for that destination chain. The caller (`AttachOutboundsToExistingUniversalTx`) treats this as a note-only failure: it stores the error string in `UniversalTx.RevertError` and returns `nil`, so the surrounding inbound-execution function (`ExecuteInboundFundsAndPayload` / `ExecuteInboundGasAndPayload`) reports success even though no `OutboundTx` was ever attached and no revert/refund path was scheduled.

### Finding Description
The lifecycle for `FUNDS_AND_PAYLOAD` inbounds is: (1) mint PRC20 to the UEA, (2) execute the universal payload against the UEA via `ExecutePayloadV2`/`CallExecuteUniversalTx`, both of which are committed EVM state changes, not part of a later rollback boundary. If that payload logic burns/transfers the PRC20 and emits a `UniversalTxOutbound` log requesting a cross-chain release (e.g. bridging funds back out), the resulting outbound is only synthesized afterward, from the receipt, in `BuildOutboundsFromReceipt`: [1](#0-0) 

If `IsChainOutboundEnabled` for the destination chain has been toggled off (an admin-only, otherwise-unprivileged registry action reachable at any time via `MsgUpdateChainConfig`) between when the inbound was voted/queued and when the payload actually executes, this check hard-fails and the outbound is dropped entirely — no `OutboundTx` object is ever created for the value that was already moved on Push Chain's EVM side.

The caller then swallows this as a soft failure: [2](#0-1) 

Note the `payloadPcTx.Status = "SUCCESS"` is already recorded, and even when `AttachOutboundsToExistingUniversalTx` errors, the function only records `RevertError` on the UTX and returns `nil` from `ExecuteInboundFundsAndPayload` — there is no automatic refund/rescue outbound scheduled in this branch (unlike the explicit `buildRevertOutbound` path used for pre-deposit/deposit failures elsewhere in the same file).

This is the direct analog of the reNFT `onStop` hook bug: a status flag that is legitimate for admins to flip (analogous to `Guard::updateHookStatus`) is checked only at the "closing"/settlement step of a flow whose "opening" step (deposit + payload execution / `onStart`) has already irreversibly happened. Because the check and the irreversible action are decoupled in time, toggling the flag between the two steps strands value that has already left the recoverable pre-execution state.

### Impact Explanation
Funds that were already deposited into the UEA and consumed/burned by the payload (in order to trigger the outbound) have no corresponding `OutboundTx` created, and no automated revert/rescue outbound is scheduled by this failure path. This matches "permanent freezing" / "unauthorized burn without corresponding release" of user-controlled funds — the exact class of impact called out in the allowed-impact gate. Recovery, if any, would depend on the manual `AttachRescueOutboundFromReceipt` rescue-flow preconditions being met, which are keyed off specific PCTx/outbound states that this silent-failure path does not necessarily satisfy (it requires either a FAILED CEA deposit or a REVERTED `INBOUND_REVERT` outbound, neither of which exists here — the deposit and payload both recorded `SUCCESS`).

### Likelihood Explanation
Reaching this state does not require any malicious behavior from an unprivileged party: it only requires (a) an inbound with a payload that produces a cross-chain outbound, and (b) the destination chain's outbound flag being disabled (for any operational/administrative reason — incident response, maintenance, deprecation) at the moment the payload executes rather than at the moment the inbound was first observed. Given `uregistry` config changes are explicitly designed to be applied live and independently of in-flight UTX processing, and there is no check at inbound-acceptance time that "locks in" outbound-enabled status for the eventual payload execution, this is a plausible, non-adversarial race that an operator could trigger unintentionally, mirroring the exact confirmed reNFT scenario.

### Recommendation
- Do not treat a disabled destination chain as a silent/soft failure once the payload's on-chain effects (deposit + burn/transfer) are already committed. Either:
  - Automatically synthesize a rescue/refund outbound (reusing the existing `RESCUE_FUNDS`/`INBOUND_REVERT` machinery) whenever `BuildOutboundsFromReceipt` rejects an outbound due to a disabled chain, so stranded value is queued for return; or
  - Make chain-outbound disablement non-retroactive for payloads that already reached execution, i.e., check `IsChainOutboundEnabled` before allowing payload execution to proceed to the point where funds are consumed, not only in the post-hoc `BuildOutboundsFromReceipt` step.
- Ensure `UniversalTx.RevertError` being set actually triggers an operational/automatic remediation path rather than only being a queryable debugging field.

### Proof of Concept
1. Admin enables inbound and outbound for `eip155:X` and configures a token.
2. A user submits a source-chain inbound of `FUNDS_AND_PAYLOAD` type whose `UniversalPayload` calls a UEA method that burns/transfers PRC20 and triggers `UniversalTxOutbound` targeting `eip155:X` (bridging value back out).
3. UVs vote and reach quorum; `ExecuteInboundFundsAndPayload` runs: PRC20 is deposited (committed), then `ExecutePayloadV2` executes the payload (committed, burns/moves the PRC20 and emits the outbound log) — see [3](#0-2) .
4. Immediately before `AttachOutboundsToExistingUniversalTx` runs (or in the same block via a preceding `MsgUpdateChainConfig`), the admin disables `IsOutboundEnabled` for `eip155:X`.
5. `BuildOutboundsFromReceipt` returns `"outbound is disabled for chain eip155:X"`; `AttachOutboundsToExistingUniversalTx` propagates the error; `ExecuteInboundFundsAndPayload` stores it in `UniversalTx.RevertError` and returns `nil` (success) — confirmed by code at [4](#0-3) .
6. Result: `utx.PcTx` shows the deposit and payload execution as `SUCCESS`, but `utx.OutboundTx` is empty and no automatic refund/rescue outbound exists — the burned/transferred value is unaccounted for on any chain.

Note: I was unable to fully trace whether the existing `AttachRescueOutboundFromReceipt` manual-rescue mechanism could later be invoked by an operator for this specific case, since its eligibility checks (CEA-deposit-failed or REVERTED `INBOUND_REVERT`) do not appear to match the state left by this silent-failure branch; this would need runtime verification with a full test harness to confirm the funds are unrecoverable versus merely requiring a currently-unimplemented remediation trigger.

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

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-298)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)

	payloadPcTx := types.PCTx{
		Sender:      ueModuleAddressStr,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "FAILED",
	}
	// Capture tx hash from receipt even on EVM revert for debugging.
	if receipt != nil {
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L309-326)
```go
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		payloadPcTx.Status = "SUCCESS"

		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```
