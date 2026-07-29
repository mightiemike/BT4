### Title
Sandwichable AMM auto-swap on inbound `GAS`/`GAS_AND_PAYLOAD` deposits due to fixed 5% slippage with no TWAP or user-supplied minimum - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The auction-bid-griefing bug class (unprivileged actor manipulates externally observable market state immediately before a protocol-forced spend of pooled/user funds, extracting value up to a bounded cap) has a structural analog in `x/uexecutor`'s auto-swap path. Whenever an inbound of type `GAS` or `GAS_AND_PAYLOAD` is finalized, or an outbound gas refund with excess gas is processed, the module fetches a live Uniswap V3 `QuoterV2` quote and immediately executes a swap with only a hard-coded 5% slippage tolerance and no TWAP/oracle cross-check. Because the triggering `MsgVoteInbound`/`MsgVoteOutbound` transactions are gasless, public mempool transactions with deterministic, predictable effects, an attacker can sandwich the swap to extract up to the full 5% slippage margin from user/protocol-owned funds.

### Finding Description
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` both call `GetSwapQuote` (a static `QuoterV2.quoteExactInputSingle` read against current pool state) and then immediately compute `minPCOut = quote * 95 / 100` before calling `CallPRC20DepositAutoSwap`, which performs the real swap on-chain: [1](#0-0) 

The same pattern recurs for the `GAS_AND_PAYLOAD` path: [2](#0-1) 

and again for excess-gas refunds on successful/failed outbounds: [3](#0-2) 

In all three call sites the only price protection is a flat 5% band computed from a quote taken moments before execution, with no TWAP, no oracle sanity check, and no user-configurable minimum. The triggering transactions — `MsgVoteInbound` and `MsgVoteOutbound` — are in the gasless whitelist and thus land in the public mempool with predictable content and predictable state effects (deposit amount, asset, and destination pool are all visible before quorum is reached): [4](#0-3) 

An attacker observing the pending third (quorum-completing) vote can submit a front-run swap against the same Uniswap V3 pool to push the price down right before the module's `depositPRC20WithAutoSwap` executes, then back-run to restore the price, capturing up to the full 5% slippage margin from the victim's deposited/refunded value. This mirrors the external report's core mechanic: an unprivileged actor manipulates externally-observable, attacker-influenceable market state immediately before a protocol-driven spend of pooled funds bounded only by a fixed cap (`maxBid` in the original report, the 5% slippage band here).

### Impact Explanation
Each exploited inbound/outbound swap directly corrupts native/PRC20 accounting for the affected `UniversalTx`: the UEA or refund recipient receives up to 5% less PC-equivalent value than the fair market price at the time of the triggering event, with the difference captured by the attacker. This is a repeatable, capital-efficient value extraction against ordinary users' bridged funds and protocol-processed refunds, reachable by any unprivileged party with capital to trade against the Uniswap V3 pool used by `UniversalCore`.

### Likelihood Explanation
Exploitation only requires: (1) monitoring the public mempool for `MsgVoteInbound`/`MsgVoteOutbound` transactions that will complete quorum and trigger an auto-swap, and (2) the ability to trade in the same pool used by `QuoterV2`/`UniversalCore` with enough capital to move price within the 5% band. No validator, TSS, or governance compromise is required, and the mechanism (quote-then-swap with a static percentage tolerance) applies to every `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound gas refund with excess gas, making it a broadly reachable, repeatable pattern rather than an edge case.

### Recommendation
Replace the static 5% slippage band with either: a TWAP-based reference price cross-checked against the spot quote before accepting the swap, a maximum allowed price-impact check independent of the instantaneous quote, or an escape hatch that skips/defers the auto-swap (falling back to a plain deposit, as already exists in the fallback path) when the quote deviates significantly from a longer-window reference price. Consider also tightening the slippage tolerance and/or adding a governance-configurable cap rather than a hard-coded `95/100` constant used at every call site.

### Proof of Concept
1. Attacker watches the mempool for the third `MsgVoteInbound` (or `MsgVoteOutbound`) vote that will push a given `UniversalTx` inbound/outbound past 2/3 quorum, knowing this will trigger `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund` with a known asset and amount (visible in the vote payload).
2. Immediately before that vote lands, attacker submits a large swap in the same Uniswap V3 pool (`prc20 -> wpc` or reverse) to move the spot price against the upcoming module swap, staying within the bound that keeps the module's `minPCOut` check (quote taken post-manipulation, still passes `quote*95/100`) satisfied.
3. The module's `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes at the manipulated price, and the UEA/refund recipient receives up to 5% less PC than fair value.
4. Attacker submits a back-run trade restoring the pool price and closing out the sandwich, netting the difference extracted from the user's bridged/refunded funds.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
						if execErr == nil {
							quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L213-231)
```go
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
```

**File:** app/README.md (L161-172)
```markdown
**The gasless whitelist** (`app/txpolicy/gasless.go`) — only these message types qualify:

```
/uexecutor.v1.MsgExecutePayload
/uexecutor.v1.MsgVoteInbound
/uexecutor.v1.MsgVoteOutbound
/uexecutor.v1.MsgVoteChainMeta
/utss.v1.MsgVoteTssKeyProcess
/utss.v1.MsgVoteFundMigration
```

A tx is gasless only if **every** message (including those nested inside `authz.MsgExec`) is in the whitelist.
```
