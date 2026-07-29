### Title
Failed excess-gas refund in `applyGasRefund` is permanently unretried, freezing the user's refundable gas fee - (File: x/uexecutor/keeper/outbound.go)

### Summary
The external report describes a CosmWasm custody contract that swaps rewards via sub-messages and, if the reply for a swap fails, permanently strands the funds until someone happens to re-trigger the same top-level function. The equivalent invariant in Push Chain is: once an outbound is finalized (via honest-validator quorum on `MsgVoteOutbound`), any excess gas fee owed back to the user must actually reach the user. In `x/uexecutor/keeper/outbound.go`, `applyGasRefund` is called exactly once, from `FinalizeOutbound` → `handleFailedOutbound`/`handleSuccessfulOutbound`, itself invoked only from `VoteOutbound` upon quorum. If both refund attempts inside `applyGasRefund` fail, the function records `PcRefundExecution.Status = "FAILED"` and `RefundSwapError`, and simply returns — there is no other code path in the repository that re-invokes `CallUniversalCoreRefundUnusedGas` for that outbound.

### Finding Description
`applyGasRefund` ( [1](#0-0) ) computes `refundAmount = gasFee - gasFeeUsed` and attempts two sequential EVM calls to `UniversalCore.refundUnusedGas`:
1. Swap path (`withSwap=true`) — depends on `GetDefaultFeeTierForToken` and `getSwapQuoteForRefund` succeeding first.
2. Fallback no-swap path (`withSwap=false`) if step 1 fails for any reason (fee-tier fetch failure, quote failure, or the EVM call itself reverting).

If the fallback call in step 2 also fails, the code path is: [2](#0-1) 
which sets `refundPcTx.Status = "FAILED"`, records the error message, and stores `outbound.RefundSwapError` — but never returns an error up the call chain, and the outbound proceeds to `Status_REVERTED`/`Status_OBSERVED` via `UpdateOutbound`. This is invoked from `handleFailedOutbound`/`handleSuccessfulOutbound` inside `FinalizeOutbound`, which in turn is only called once, from `VoteOutbound` on quorum: [3](#0-2) .

Once the outbound reaches a terminal status (`REVERTED` or `OBSERVED` with the refund attempt already made and recorded as `FAILED`), no other function in the module re-attempts `applyGasRefund` for that outbound. Repo-wide search found no retry mechanism (`Retry`, `RetryRefund`, `RetryGasRefund` all return zero matches), and `PcRefundExecution`/`RefundSwapError` are only ever written once and never read back to drive a re-attempt. Both refund paths are gated by external, attacker-uninfluenced but still-fallible conditions: `GetDefaultFeeTierForToken`, on-chain swap quote liquidity/slippage (`minPCOut`), and the `UniversalCore.refundUnusedGas` EVM call itself, any of which can transiently or persistently fail (e.g., temporary liquidity issues, gas-limit exhaustion inside `DerivedEVMCall`, or `UniversalCore` contract-level reverts) without any attacker action being required.

This differs from the referenced Anchor bug in one respect: the Anchor custody contract at least allows funds to be un-stuck by anyone re-triggering `swap_to_stable_denom` in a future round. Here, once quorum finalizes `MsgVoteOutbound` and both refund attempts fail, the excess gas fee the user was already charged and is entitled to reclaim is architecturally unrecoverable — there is no subsequent call site, admin message, or automatic retry that revisits a `FAILED` `PcRefundExecution`.

### Impact Explanation
This falls within the explicitly in-scope "corruption of ... gas fee accounting, refund accounting" and "permanent loss ... of user or protocol-controlled funds" categories. The excess gas fee (`gasFee - gasFeeUsed`) that was already deducted from the user for the destination-chain transaction is left permanently un-refunded once both refund attempts fail, with the failure state (`PcRefundExecution.Status = "FAILED"`) being terminal and unrecoverable through any code path in the module. This is a direct, unprivileged-reachable loss of user funds — it requires no malicious validator, relayer, or admin action; it is simply a function of ordinary destination-chain gas fluctuation combined with swap-liquidity or contract-level failures at the moment quorum happens to finalize.

### Likelihood Explanation
Triggering the underlying failure conditions (insufficient DEX liquidity for the gas-token→PC swap, quote/fee-tier lookup failure, or an EVM-level revert inside `UniversalCore.refundUnusedGas`) is plausible under ordinary operating conditions and does not require any privileged actor — it can happen whenever `refundUnusedGas` reverts for either the swap or the plain-deposit path in the same block that validators finalize the vote. Given that `FinalizeOutbound` runs exactly once per outbound and there is no retry, the likelihood of the failure state becoming permanent is effectively 100% whenever both attempts fail once, which is a realistic, externally-triggerable degenerate case (e.g., low liquidity for a long-tail gas token, or `UniversalCore` running low on the swap-out asset).

### Recommendation
Add a retry/reconciliation path for outbounds whose `PcRefundExecution.Status == "FAILED"`. Options: (a) an admin- or permissionless-callable `MsgRetryGasRefund` message that re-invokes `applyGasRefund` for a specific finalized outbound, guarded by idempotency checks against `PcRefundExecution`; or (b) a periodic `BeginBlocker`/`EndBlocker` sweep over outbounds with `RefundSwapError` set and `PcRefundExecution.Status == "FAILED"` that retries the no-swap fallback. Ensure such retries are idempotent (do not double-refund) by checking `PcRefundExecution.Status` before attempting, and emit a distinct event so off-chain monitoring can alert when refunds are stuck.

### Proof of Concept
1. A user submits a payload that produces an outbound with a nonzero `GasFee` denominated in a low-liquidity `GasToken`.
2. Universal Validators observe the destination-chain execution and vote via `MsgVoteOutbound`, reporting `GasFeeUsed < GasFee` so an excess refund is due; quorum is reached with honest validators.
3. `VoteOutbound` → `FinalizeOutbound` → `handleSuccessfulOutbound`/`handleFailedOutbound` → `applyGasRefund` runs: `GetDefaultFeeTierForToken`/`getSwapQuoteForRefund` fails, or the swap-based `CallUniversalCoreRefundUnusedGas` reverts (e.g., `minPCOut` slippage check fails on-chain due to low liquidity).
4. The no-swap fallback `CallUniversalCoreRefundUnusedGas(..., withSwap=false, ...)` is attempted; if `UniversalCore.refundUnusedGas`'s no-swap branch also reverts (e.g., insufficient PRC20 balance backing the gas token in `UniversalCore`, or a transient EVM gas-limit issue in `DerivedEVMCall`), `refundPcTx.Status = "FAILED"` is recorded permanently on the outbound: [2](#0-1) .
5. The outbound is now terminal (`REVERTED` or `OBSERVED`); no further code path in the repository (verified via repo-wide search for retry-related identifiers) ever revisits this outbound's refund, so the user's excess gas fee is permanently unrecoverable.

### Citations

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

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L131-146)
```go
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
