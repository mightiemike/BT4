Confirmed: in `handleFailedOutbound` (x/uexecutor/keeper/outbound.go:102-161), when `CallPRC20Deposit` (the re-mint that returns bridged funds to the revert recipient) fails, the function returns immediately via `k.AbortOutbound(...)` on line 135-136 — **before** reaching `k.applyGasRefund(ctx, &outbound, obs)` on line 158. This means the pre-paid relayer/gas fee (`outbound.GasFee`, taken upfront from the user when the outbound was created on the EVM `UniversalGatewayPC` side) is permanently forfeited whenever the outbound ends in `ABORTED` status, even though `obs.GasFeeUsed` (reported by validators) may show substantial unused gas that would otherwise trigger a refund via `applyGasRefund` (x/uexecutor/keeper/outbound.go:174-257). [1](#0-0) ### Title
Gas fee paid upfront by user is permanently forfeited when a failed outbound is ABORTED instead of REVERTED - (File: x/uexecutor/keeper/outbound.go)

### Summary
This is a direct analog of the Sablier finding: the relayer/protocol gas fee for an outbound (`OutboundTx.GasFee`) is collected from the user in full at outbound-creation time (via the `UniversalGatewayPC` withdraw call on the EVM side, captured into `OutboundTx.GasFee`/`GasToken` fields in `BuildOutboundsFromReceipt`, see `x/uexecutor/keeper/create_outbound.go:69-91`). The excess between this pre-paid fee and the actually-used gas (`ObservedTx.GasFeeUsed`) is normally refunded via `applyGasRefund` (`x/uexecutor/keeper/outbound.go:174-257`) for both successful and reverted outbounds. However, one failure path — where the fund-return mint fails and the outbound is marked `ABORTED` — returns before the refund logic ever runs, so the pre-paid fee (or its unused excess) is never refunded, mirroring the "fee paid in full up front, never adjusted when the flow is cut short" defect described in the report.

### Finding Description
`FinalizeOutbound` (`x/uexecutor/keeper/outbound.go:71-97`) routes a failed observed outbound to `handleFailedOutbound`. Inside `handleFailedOutbound` (`x/uexecutor/keeper/outbound.go:99-161`), when the tx type is fund-related, the keeper attempts to re-mint the bridged PRC20 back to the revert recipient via `k.CallPRC20Deposit(...)` (line 119). If that EVM call errors:

```
if err != nil {
    pcTx.Status = "FAILED"
    ...
    outbound.PcRevertExecution = &pcTx
    // Re-mint failed — mark as ABORTED for manual intervention
    return k.AbortOutbound(ctx, utxId, outbound, ...)
}
``` [2](#0-1) 

the function returns immediately. `k.applyGasRefund(ctx, &outbound, obs)` at line 158 — the only place that compares `outbound.GasFee` (pre-paid) against `obs.GasFeeUsed` (actually consumed on the destination chain) and refunds the difference — is never reached on this path. [3](#0-2) 

By contrast, the "successful revert" branch of the same function (re-mint succeeds) and `handleSuccessfulOutbound` (`x/uexecutor/keeper/outbound.go:163-172`) both call `applyGasRefund` unconditionally. The code comment even states the refund is meant to run "regardless of tx type" and "regardless of execution outcome," but the ABORTED early-return silently violates that invariant.

The upfront fee itself is fixed at outbound-creation time by `getOutboundTxGasAndFees` on the `UniversalCore`/`UniversalGatewayPC` contracts and stored verbatim into `OutboundTx.GasFee` (`x/uexecutor/keeper/gas_fee.go:26-64`, `x/uexecutor/keeper/create_outbound.go:69-91`) — exactly analogous to Sablier's broker/protocol fee being computed and paid in full at stream-creation time regardless of what happens later.

### Impact Explanation
Whenever an outbound ends up `ABORTED` due to the re-mint call failing, the user's already-paid relayer gas fee (and any of it that would have been excess/unused, per `ObservedTx.GasFeeUsed`) is permanently stuck — it is neither delivered to a relayer for a broadcast tx (the outbound never completed) nor refunded to the user. This is an unrecoverable loss of user funds on the corrupted `OutboundTx.PcRefundExecution` state (which stays `nil` forever, since `ABORTED` is a terminal status requiring manual admin intervention and the normal finalize path is never re-entered for it). This matches "permanent loss...of user...funds" in the allowed-impact gate.

### Likelihood Explanation
The trigger condition — `CallPRC20Deposit` reverting when re-minting the bridged token to the revert recipient — is influenced by attacker-controlled inputs: the recipient is `outbound.RevertInstructions.FundRecipient` if set, else `outbound.Sender`, both of which originate from the original inbound/outbound-creating user (`x/uexecutor/keeper/outbound.go:107-112`). An honest set of Universal Validators votes the outbound as failed via `MsgVoteOutbound`; whether the PRC20 mint then reverts depends on properties of the recipient/token (e.g., a recipient address that a PRC20 contract or its underlying mint hook rejects). This is a narrower trigger than a fully generic "any user, anytime," so likelihood is moderate rather than certain — but the underlying code defect (skipping the refund call on the abort path) is deterministic and always present regardless of how the abort is reached, including future PRC20 tokens or edge conditions that make the mint fail.

### Recommendation
Call `k.applyGasRefund(ctx, &outbound, obs)` before (or independently of) the `AbortOutbound` early return, so that the excess/pre-paid gas fee is refunded regardless of whether the fund re-mint succeeds. Structuring the function so the gas-fee refund and the fund re-mint are two independent, unconditionally-executed steps (as the existing code comment already claims) removes the forfeiture path.

### Proof of Concept
1. A user submits an inbound that generates an outbound with `TxType_FUNDS` (or `FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD`) and a `RevertInstructions.FundRecipient` chosen by the user.
2. Universal Validators observe the outbound failed on the destination chain and vote via `MsgVoteOutbound` with `success=false` and `gas_fee_used` well below `outbound.GasFee` (large excess).
3. `FinalizeOutbound` → `handleFailedOutbound` attempts `CallPRC20Deposit` to re-mint funds to `FundRecipient`; this call reverts (e.g., because the chosen `FundRecipient`/token combination causes the mint to fail).
4. `AbortOutbound` is invoked and the function returns before `applyGasRefund` executes.
5. The outbound is now `Status_ABORTED` with `PcRefundExecution == nil` forever — the pre-paid gas fee excess is never refunded, unlike the `Status_REVERTED` and `Status_OBSERVED` cases where `applyGasRefund` always runs (as demonstrated by the passing test `TestGasFeeRefund/"failed outbound performs both revert and gas refund"` in `test/integration/uexecutor/gas_fee_refund_test.go:199-239`, which only covers the non-aborted revert path and does not exercise the abort branch).

### Citations

**File:** x/uexecutor/keeper/outbound.go (L130-161)
```go
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
```
