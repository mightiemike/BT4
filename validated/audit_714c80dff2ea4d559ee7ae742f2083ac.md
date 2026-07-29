### Title
Incomplete `TxType` coverage in `handleFailedOutbound` permanently loses bridged/gas value for `GAS` and `PAYLOAD` outbounds - (File: x/uexecutor/keeper/outbound.go)

### Summary
The external report (H-7) is about protocol code that only "claims"/handles the reward-recovery path for some cases (`CompoundProvider`) while leaving the analogous recovery logic empty for others (`AaveProvider`, `BetaProvider`), silently losing value that should have been recovered. The same incompleteness pattern exists in Push Chain's outbound-finalization path: only a subset of `TxType` values trigger the fund-recovery ("revert") branch when a destination-chain execution fails, while other `TxType`s that can carry value are silently excluded, permanently stranding the associated funds.

### Finding Description
`FinalizeOutbound` → `handleFailedOutbound` in [1](#0-0)  only re-mints bridged tokens back to the user when the outbound's `TxType` is `FUNDS`, `GAS_AND_PAYLOAD`, or `FUNDS_AND_PAYLOAD`: [2](#0-1) 

Per the module's own documented `TxType` semantics table [3](#0-2) , `TxType_GAS` as an outbound means "Refund of unused gas back to a source chain" — i.e. it moves value out of Push Chain just like `FUNDS`/`FUNDS_AND_PAYLOAD` do, and `TxType_PAYLOAD` outbounds are documented as carrying no value ("Pure call on the destination chain"). However, `handleFailedOutbound`'s revert-fund gate omits `TxType_GAS` entirely (it also omits `PAYLOAD`, but that case is value-free by design and therefore not exploitable). Because value that was already deducted/burned on Push Chain to fund the `GAS`-type outbound is never re-minted when UVs report the destination-chain execution as failed (`obs.Success == false`), the outbound is simply marked `REVERTED` with no compensating mint, and only `applyGasRefund` for the unrelated *excess relayer-gas* leg runs (which is a separate accounting bucket keyed off `GasFee`/`GasFeeUsed`, not the outbound's principal `Amount`).

This mirrors the audited bug precisely: the developers implemented the "claim"/recovery logic for the code paths they anticipated (`FUNDS`, `FUNDS_AND_PAYLOAD`, `GAS_AND_PAYLOAD`), but left the analogous path for `GAS` outbounds without a symmetric recovery branch, assuming (incorrectly, per the module's own doc) that it does not need one.

### Impact Explanation
Any ordinary user whose gas-refund outbound (`TxType_GAS`) is honestly voted as failed by UVs (e.g., destination-chain relay reverted, insufficient gas on destination, transient RPC/broadcast failure surfaced through the vote) has their refunded principal amount permanently lost: it is not re-minted on Push Chain, and the outbound record is marked terminal (`REVERTED`), leaving no automatic recovery path. This is a direct, unprivileged-user-reachable permanent loss of protocol/user funds, which is explicitly in-scope ("permanent loss ... of user or protocol-controlled funds", "corruption of ... refund accounting").

### Likelihood Explanation
Reachable by any user who triggers a `GAS`-type cross-chain flow whose destination-side leg is voted as failed by honest, non-malicious UVs — no privileged actor or malicious validator is required, only ordinary destination-chain execution failure (gas spikes, reverts, congestion), which is a realistic and common occurrence in cross-chain relaying.

### Recommendation
Extend `handleFailedOutbound`'s fund-recovery condition to also cover `TxType_GAS` (and any other `TxType` variants that can carry non-zero `Amount`/`Prc20AssetAddr`), or derive the "does this outbound carry value" check generically from `outbound.Amount != "0"` / `Prc20AssetAddr != ""` rather than hard-coding a subset of `TxType` values, so newly added or previously overlooked value-carrying outbound types are not silently excluded from the revert path.

### Proof of Concept
1. A user submits an inbound whose payload drives Push Chain to create a `TxType_GAS` outbound (fee-abstraction gas refund back to source chain) with `Amount > 0` and `Prc20AssetAddr` set to the relevant PRC20/native representation; the principal is deducted on Push Chain to fund this outbound (see outbound creation in `x/uexecutor/keeper/create_outbound.go`, not fully inspected here but implied by `OutboundTx.Amount`/`Prc20AssetAddr` fields).
2. UVs broadcast the outbound to the destination chain; the destination-chain execution fails for a benign reason (e.g., reverted tx, gas underpricing).
3. UVs honestly vote `MsgVoteOutbound` with `success=false` via `VoteOutbound` [4](#0-3) .
4. `FinalizeOutbound` calls `handleFailedOutbound`; since `outbound.TxType == types.TxType_GAS`, the fund re-mint block at lines 104-147 is skipped entirely, and the outbound transitions straight to `Status_REVERTED` at line 149 with no compensating `CallPRC20Deposit`.
5. The user's principal value for that gas-refund outbound is permanently unrecoverable through the normal protocol flow — contrast with `TestOutboundVoting`'s `"outbound failure triggers revert execution"` case [5](#0-4) , which only asserts correct revert behavior for tx types that are actually covered by the guard, leaving the `GAS` case untested and unhandled.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L99-147)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}
```

**File:** x/uexecutor/README.md (L128-136)
```markdown
| `TxType` | Inbound semantics | Outbound semantics |
|---|---|---|
| `GAS` | User pre-paid gas on the source chain. Mints PC to the recipient as a gas top-up. | Refund of unused gas back to a source chain. |
| `GAS_AND_PAYLOAD` | Gas top-up + executes a payload through the recipient's UEA in the same Push Chain tx. | Same combo on the destination side. |
| `FUNDS` | Pure synthetic transfer — mints PRC20 representation of an external token. | Pure transfer of a PRC20 back out of Push Chain. |
| `FUNDS_AND_PAYLOAD` | Mints funds + runs a payload (e.g. deposit + DEX swap atomically). | Funds delivery with a destination-side call. |
| `PAYLOAD` | Pure payload execution, no value movement. | Pure call on the destination chain. |
| `INBOUND_REVERT` | Reverts a previously-executed inbound (returns funds to the source-chain sender). | — |
| `RESCUE_FUNDS` | Admin-driven rescue path for stuck funds. | Outbound that delivers the rescue. |
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L110-147)
```go
	// Step 5: Update outbound state to OBSERVED
	outbound.OutboundStatus = types.Status_OBSERVED
	outbound.ObservedTx = &observedTx

	k.Logger().Info("outbound observed",
		"utx_id", utxId,
		"outbound_id", outboundId,
		"success", observedTx.Success,
		"dest_chain", outbound.DestinationChain,
	)

	// Persist the state inside UniversalTx
	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Remove from pending outbounds index now that status is OBSERVED
	if err := k.PendingOutbounds.Remove(ctx, outboundId); err != nil {
		return fmt.Errorf("failed to remove pending outbound index for %s: %w", outboundId, err)
	}

	// Step 6: Finalize outbound (refund if failed).
	// If re-mint fails, handleFailedOutbound marks it ABORTED internally and returns nil.
	// Business logic errors are stored in RevertError on the UTX; only infra errors are returned.
	if err := k.FinalizeOutbound(ctx, utxId, outbound); err != nil {
		k.Logger().Error("outbound finalization error stored on utx",
			"utx_id", utxId,
			"outbound_id", outboundId,
			"error", err.Error(),
		)
		if storeErr := k.UpdateUniversalTx(ctx, utxId, func(u *types.UniversalTx) error {
			u.RevertError = err.Error()
			return nil
		}); storeErr != nil {
			return storeErr
		}
	}

```

**File:** test/integration/uexecutor/vote_outbound_test.go (L224-261)
```go
	t.Run("outbound failure triggers revert execution", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// Reach quorum with FAILED observation
		for i := 0; i < 3; i++ {
			valAddr, _ := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			coreAcc := sdk.AccAddress(valAddr).String()

			err := utils.ExecVoteOutbound(
				t,
				ctx,
				app,
				vals[i],
				coreAcc,
				utxId,
				outbound,
				false,
				"execution reverted", // revert reason
				outbound.GasFee,      // gas_fee_used required; use full fee → no excess refund
			)
			require.NoError(t, err)
		}

		utx, _, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)

		fmt.Println(utx)

		ob := utx.OutboundTx[0]

		require.Equal(t, uexecutortypes.Status_REVERTED, ob.OutboundStatus)
		require.NotNil(t, ob.PcRevertExecution)

		pc := ob.PcRevertExecution
		require.Equal(t, "SUCCESS", pc.Status)
		require.NotEmpty(t, pc.TxHash)
	})
```
