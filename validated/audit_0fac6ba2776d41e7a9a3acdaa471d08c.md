### Title
Gas-refund and revert-mint accounting marks outbound recovery as "SUCCESS" without verifying the underlying PRC20/UniversalCore call actually delivered funds - (File: x/uexecutor/keeper/outbound.go)

### Summary
The audit's core pattern — a `withdraw()` function computes an expected token amount and transfers it, but never verifies the destination actually received that amount before recording success — has a direct analog in Push Chain's outbound-recovery path. `applyGasRefund` and `handleFailedOutbound` in `x/uexecutor/keeper/outbound.go` compute a refund/re-mint amount, issue a `DerivedEVMCall` to `UniversalCore`/PRC20, and mark the operation `"SUCCESS"` purely because the Go-level `err` returned by `DerivedEVMCall` is `nil` — without inspecting the underlying EVM execution result (e.g., a reverted inner call, a no-op due to a mis-set allowance/balance check inside `refundUnusedGas`/`depositPRC20Token`) for confirmation that value actually moved.

### Finding Description
`applyGasRefund` computes `refundAmount := gasFee - gasFeeUsed` and calls `k.CallUniversalCoreRefundUnusedGas(...)`, then does: [1](#0-0) 
setting `refundPcTx.Status = "SUCCESS"` solely because `err == nil`. Likewise the fallback path: [2](#0-1) 
does the same. `handleFailedOutbound` mints back bridged funds via `CallPRC20Deposit` and marks `pcTx.Status = "SUCCESS"` under the same `err == nil` condition: [3](#0-2) 

All of these calls go through `DerivedEVMCall`, whose only documented/returned artifacts are `Hash`, `GasUsed`, `Logs`, and `Ret`: [4](#0-3) 
Nowhere in `x/uexecutor` is the receipt inspected for an EVM-level revert indicator (a `VmError`/failed-status field) before the code commits `"SUCCESS"` to `PcRefundExecution` / `PcRevertExecution`. A `grep` across the repository confirms no `VmError` check exists anywhere in `x/uexecutor/keeper`. This mirrors the audited bug exactly: the code computes the expected amount, issues the value-moving call, and treats a non-error Go return as proof the tokens were delivered — without checking the actual on-chain outcome.

### Impact Explanation
If the `UniversalCore.refundUnusedGas` or `Handler.depositPRC20Token` contract call reverts internally (e.g., insufficient PRC20 supply/allowance inside the swap leg, a paused token, or any state that only manifests as an EVM-level revert rather than a Go error from `DerivedEVMCall`), the outbound would be recorded with `PcRefundExecution.Status == "SUCCESS"` or `PcRevertExecution.Status == "SUCCESS"` even though the user never received the refunded gas or the re-minted bridged funds. Because `UniversalTx`/`OutboundTx` state carries no separate on-chain-verified balance check, this becomes a permanent silent loss of user funds recorded as a successful recovery — an unauthorized/incorrect accounting state that cannot be retried (the outbound is already marked `OBSERVED`/`REVERTED` and the refund/revert leg is considered closed).

### Likelihood Explanation
This is contingent on whether the fork's `DerivedEVMCall` implementation (in the separate `github.com/pushchain/evm` module, not present in this repo) surfaces an inner-call revert as a non-nil Go `error` or only as a `VmError`/failed status field on the returned receipt with `err == nil`. That implementation is not available in this codebase to confirm definitively. If `DerivedEVMCall` always converts contract-level reverts into a Go error (consistent with how `k.evmKeeper.CallEVM` failures are handled elsewhere), then the accounting is actually sound and this is a false positive. I could not verify this because the fork's source is out of scope/not indexed.

### Recommendation
Regardless of the fork's exact revert-to-error mapping, harden the accounting path defensively: after each `DerivedEVMCall` used for fund-moving operations (`CallPRC20Deposit`, `CallUniversalCoreRefundUnusedGas`, `CallExecuteUniversalTx`), explicitly check the returned receipt for a VM-level failure indicator (if the fork's `MsgEthereumTxResponse` exposes one) in addition to the Go `error`, and only mark `Status = "SUCCESS"` when both are clean. Where no such receipt field exists, verify success via a post-call balance/state read (e.g., re-query PRC20 balance of the recipient before/after) before committing `"SUCCESS"` to `PcRefundExecution`/`PcRevertExecution`, matching the audit's own recommendation to "double-check the balance difference."

### Proof of Concept
Not constructible from this repository alone: reproducing the scenario requires forcing an EVM-level revert inside `UniversalCore.refundUnusedGas` or `Handler.depositPRC20Token` while `DerivedEVMCall` returns `err == nil`, which depends on internal behavior of the `github.com/pushchain/evm` fork that is not present in this codebase. This finding should be treated as a defense-in-depth gap pending confirmation of the fork's error-surfacing semantics via a Devin session with access to the `pushchain/evm` module source.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L119-141)
```go
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
```

**File:** x/uexecutor/keeper/outbound.go (L223-230)
```go
			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
			if err == nil {
				refundPcTx.TxHash = resp.Hash
				refundPcTx.GasUsed = resp.GasUsed
				refundPcTx.Status = "SUCCESS"
				outbound.PcRefundExecution = refundPcTx
				return
			}
```

**File:** x/uexecutor/keeper/outbound.go (L245-256)
```go
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
```

**File:** DERIVED_TRANSACTIONS.md (L66-74)
```markdown
The return type is `*evmtypes.MsgEthereumTxResponse`, the same type a normal `MsgEthereumTx` produces. Concretely:

```go
receipt, err := k.evmKeeper.DerivedEVMCall(...)
// receipt.Hash    -- 0x... tx hash, queryable via eth_getTransactionByHash
// receipt.GasUsed -- real gas used, observable in receipts
// receipt.Logs    -- real EVM logs, indexable by event subscribers
// receipt.Ret     -- ABI-encoded return data (for view-style commits)
```
```
