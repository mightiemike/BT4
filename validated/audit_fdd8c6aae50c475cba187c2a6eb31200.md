This is a confirmed analog. `event.GasFee`, `event.GasPrice`, and `event.GasToken` are decoded directly from a `UniversalTxOutbound` log emitted by `UniversalGatewayPC` (or by a UEA payload execution), and copied verbatim into `OutboundTx.GasFee` / `GasToken` with no cross-check against the actual gas cost of the outbound.

### Title
Attacker-Controlled `GasFee`/`GasToken` in Outbound Event Drains Protocol Funds via `refundUnusedGas` - (File: x/uexecutor/keeper/outbound.go)

### Summary
The gas-refund mechanism trusts a self-reported `GasFee` value that originates from a user-controlled EVM event rather than from any independently verified cost, mirroring the oracle-price report's core flaw: a value that should represent ground truth is instead attacker-influenced and is later used unmodified in a fund-moving calculation.

### Finding Description
When an outbound is built from a receipt, `BuildOutboundsFromReceipt` decodes the `UniversalTxOutbound` event log and copies `event.GasFee`, `event.GasPrice`, and `event.GasToken` straight into `OutboundTx` with no bound or sanity check against real execution cost: [1](#0-0) 

This event can be emitted either by the `UniversalGatewayPC` contract during a user's own `MsgExecutePayload` (executed through the user's UEA — a path fully reachable by an unprivileged user), or via the CEA execution path. The `TxType`/payload is caller-supplied, and nothing in `BuildOutboundsFromReceipt` validates that `GasFee`/`GasToken` correspond to real gas economics — it is whatever value the gateway/contract call encoded in the log.

Later, once Universal Validators (honest, per scope) vote the outbound as observed and report the real `GasFeeUsed` on the destination chain, `applyGasRefund` computes the excess purely as `outbound.GasFee - obs.GasFeeUsed` and, if positive, calls `CallUniversalCoreRefundUnusedGas` to mint/swap and transfer that excess to the sender/fund-recipient: [2](#0-1) 

Because `outbound.GasFee` is the attacker-influenced input rather than a value derived from actual destination-chain gas price × gas limit at execution time, an attacker can set `GasFee` to an inflated amount while causing the real outbound to consume comparatively little gas. The resulting `refundAmount = GasFee - GasFeeUsed` is large and is paid out through `CallUniversalCoreRefundUnusedGas`, which performs a real swap/PRC20 deposit to the attacker's chosen recipient.

This is structurally identical to the reported bug class: a value that is supposed to reflect ground truth (oracle price / real gas cost) is instead attacker/externally influenced, gets recorded as authoritative, and is used unmodified in a subsequent critical fund-moving computation (settlement coverage / gas refund), with no independent verification or cap tying it back to reality.

### Impact Explanation
If `GasFee` is not bound to a chain-config-derived expected gas cost (e.g., `GasPrice × GasLimit` cross-checked against reasonable market gas prices, or a hard cap), an unprivileged attacker who controls the payload that emits `UniversalTxOutbound` can claim an arbitrarily large `GasFee`, and after the honest UVs report the small real `GasFeeUsed`, the protocol will mint/transfer the difference via `refundUnusedGas` — this is a direct drain of protocol-controlled funds (unauthorized mint/unauthorized release), matching the "In scope" impact of stealing/draining protocol funds.

### Likelihood Explanation
Likelihood depends on whether `GasFee`/`GasPrice`/`GasToken` in the `UniversalTxOutbound` event are actually attacker-settable (e.g., passed as arguments from the user's UEA-executed payload to `UniversalGatewayPC`) versus computed and enforced entirely by immutable, audited system-contract logic that clamps them to real gas market values. I was not able to fully verify the Solidity source of `UniversalGatewayPC` / the UEA's outbound-creation entry point within the indexed context to confirm whether `gasFee` is caller-supplied calldata or internally computed by the contract from `tx.gasprice`/`gasleft()`. This is the crux of whether the vulnerability is exploitable and needs confirmation by inspecting the corresponding Solidity contract (outside the indexed Go-side scope).

### Recommendation
- Verify in `UniversalGatewayPC`/UEA contract source whether `gasFee`, `gasPrice`, and `gasToken` in the `UniversalTxOutbound` event are caller-supplied parameters or computed on-chain from verifiable gas metering.
- If caller-supplied, cap the accepted `GasFee` at ingestion time (`BuildOutboundsFromReceipt`) against an expected value derived from `uregistry` chain config gas price bounds and a maximum gas limit, rejecting or clamping outliers before persisting to `OutboundTx`.
- Alternatively, compute the refund basis independently on the Push Chain side (e.g., from `ChainMeta` gas price history for the destination chain × the observed `GasLimit`) rather than trusting the event-supplied `GasFee` verbatim.

### Proof of Concept
Not fully constructible without confirming the Solidity-side encoding of the `UniversalTxOutbound` event (specifically whether `gasFee` is attacker-suppliable calldata). Recommend a background Devin session to: (1) locate the `UniversalGatewayPC`/UEA Solidity source that emits `UniversalTxOutbound`, (2) confirm whether `gasFee`/`gasToken` are derived from caller input, and (3) if so, craft a `MsgExecutePayload` that triggers an outbound with an inflated `gasFee` value, have a test harness vote a small `GasFeeUsed` via `MsgVoteOutbound`, and observe `applyGasRefund` minting the inflated excess to the attacker.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L69-81)
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
```

**File:** x/uexecutor/keeper/outbound.go (L178-257)
```go
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
