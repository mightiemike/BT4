## Analysis

The Sherlock bug is a classic **"computed refund amount never actually delivered to the user"** pattern: the code calculates exactly how much should be returned, but the final transfer step is either missing or silently swallowed on failure, leaving funds permanently stuck.

The closest structural analog in Push Chain's scoped code is in the outbound gas-refund path, `applyGasRefund` in [1](#0-0) , called from both `handleFailedOutbound` and `handleSuccessfulOutbound` [2](#0-1) .

### Title
Excess outbound gas fee is permanently lost when both refund attempts fail, with no retry path - (File: `x/uexecutor/keeper/outbound.go`)

### Finding Description
When a Universal Validator votes an outbound observation with `gasFeeUsed < gasFee`, `applyGasRefund` correctly computes `refundAmount = gasFee - gasFeeUsed` [3](#0-2) . It then attempts two delivery paths in sequence:

1. A swap-based refund via `CallUniversalCoreRefundUnusedGas(..., withSwap=true, ...)` [4](#0-3) .
2. On any failure (fee-tier lookup, quote fetch, or swap call error), a fallback no-swap call to the same function [5](#0-4) .

If **both** attempts fail, the code simply records `refundPcTx.Status = "FAILED"` and stores it on `outbound.PcRefundExecution` [6](#0-5) . `applyGasRefund` returns nothing (`void`) — the caller (`handleFailedOutbound`/`handleSuccessfulOutbound`) never inspects the refund outcome and unconditionally proceeds to `k.UpdateOutbound(ctx, utxId, outbound)` [7](#0-6) , which finalizes the outbound into a terminal state (`OBSERVED` or `REVERTED`).

Once an outbound reaches a terminal `outbound_status`, there is no code path in the repository (`RetryRefund`, admin-driven retry, etc.) that re-attempts the failed `pc_refund_execution` — confirmed by the absence of any refund-retry message in `x/uexecutor` [8](#0-7) . The test suite explicitly documents this behavior as accepted ("refund failure does not revert the outbound") [9](#0-8) .

This mirrors the Sherlock H-3 pattern precisely: an amount that the protocol has already computed as owed back to the user (`amountLpWithdraw` there, `refundAmount` here) is dropped on a failure branch instead of being retried or otherwise guaranteed to reach the user.

### Impact Explanation
The excess gas fee the user pre-paid on the source chain (the difference between quoted `gasFee` and actually-consumed `gasFeeUsed`) is permanently unrecoverable once both refund legs fail. This is a **permanent loss of user funds** — falling under the in-scope impact "permanent loss ... of user or protocol-controlled funds" and "corruption of ... refund accounting."

### Likelihood Explanation
This does not require validator or admin misbehavior — it only requires that `CallUniversalCoreRefundUnusedGas` reverts twice for a given `gasToken` (e.g., the swap-refund leg fails due to insufficient liquidity/slippage on the DEX pool for the configured gas token, and the no-swap fallback also reverts, e.g., because the module account cannot mint/transfer that PRC20 to the recipient for a legitimate on-chain reason). Since `GasToken` is a chain-level configured PRC20 (not attacker-chosen per transaction), the most likely trigger is a token/liquidity edge case, an ordinary UEA/recipient issue (e.g., blacklisted or non-receiving recipient), or thin-liquidity conditions that a user could plausibly influence by targeting the DEX pool used by `getSwapQuoteForRefund`. I could not fully verify from the available code how easily this dual-failure can be forced by a purely unprivileged actor versus needing an unusual configuration/liquidity state — this is the main uncertainty in likelihood.

### Recommendation
- Make `applyGasRefund` return an explicit error/result to its callers.
- On dual-refund failure, either (a) do not finalize the outbound into a terminal status until the refund succeeds, or (b) persist the failed refund into a retryable queue (analogous to `PendingOutbounds`) and expose an admin/permissionless retry message (similar to `MsgRevertStuckInbound`) so the excess gas fee can eventually be delivered instead of being permanently dropped.

### Proof of Concept
Not independently reproduced against a running node; based on static analysis of `applyGasRefund` and its callers, plus the integration test `TestGasFeeRefund/swap fallback reason stored when swap refund fails` [10](#0-9)  which already demonstrates that a refund failure does not block outbound finalization, and no subsequent code path re-attempts the failed refund.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L99-172)
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

	outbound.OutboundStatus = types.Status_REVERTED
	k.Logger().Info("outbound reverted",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)

	// Refund excess gas regardless of tx type — gas was consumed on the external
	// chain whether the execution succeeded or failed.
	k.applyGasRefund(ctx, &outbound, obs)

	return k.UpdateOutbound(ctx, utxId, outbound)
}

// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-257)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

	// Step 1: try refund with swap (gasToken → PC native)
	fee, swapErr := k.GetDefaultFeeTierForToken(ctx, gasToken)
	var swapFallbackReason string

	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
			swapFallbackReason = fmt.Sprintf("swap refund failed: %s", err.Error())
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
}
```

**File:** x/uexecutor/README.md (L197-209)
```markdown
## Messages (`MsgServer`)

| Message | Authority | Gasless? | Purpose |
|---|---|---|---|
| `MsgVoteInbound` | bonded UV | yes | Vote an observed source-chain inbound |
| `MsgVoteOutbound` | bonded UV | yes | Vote that an outbound was broadcast (or failed) on the destination chain |
| `MsgVoteChainMeta` | bonded UV | yes | Vote on observed gas price + block height for a chain |
| `MsgExecutePayload` | any | yes | Execute a payload on a UEA (the UEA itself authenticates via `verificationData`) |
| `MsgUpdateParams` | gov | no | Update module params |

> **UEA migration is now part of payload execution.** There used to be a separate `MsgMigrateUEA` message; that path has been removed. UEAs are upgraded by submitting a normal `MsgExecutePayload` whose payload calls the UEA's migration entry point on the EVM side. The Cosmos layer no longer has a dedicated migration message — the UEA contract is the source of truth for who is allowed to migrate it and to what implementation.

Vote messages check `IsBondedUniversalValidator` and `IsTombstonedUniversalValidator` on `x/uvalidator` before accepting the vote. Tombstoned validators are silently rejected.
```

**File:** test/integration/uexecutor/gas_fee_refund_test.go (L155-197)
```go
	t.Run("swap fallback reason stored when swap refund fails", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// gasFee = 111, gasFeeUsed = 1 → large excess triggers refund attempt.
		// The test handler's defaultFeeTier for an unknown gas token will either
		// return 0 or fail, causing the swap path to fall back. In either case
		// RefundSwapError must be non-empty (swap was not clean) or PcRefundExecution
		// must exist.
		gasFeeUsed := "1"

		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			coreAcc := sdk.AccAddress(valAddr).String()

			err = utils.ExecVoteOutbound(
				t,
				ctx,
				app,
				vals[i],
				coreAcc,
				utxId,
				outbound,
				true,
				"",
				gasFeeUsed,
			)
			require.NoError(t, err)
		}

		utx, _, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)

		ob := utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)

		// Refund execution must always be recorded when excess gas exists
		require.NotNil(t, ob.PcRefundExecution)

		// The outbound status stays OBSERVED (refund failure does not revert the outbound)
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)
	})
```
