## Analysis: Slippage Protection Analog in Push Chain Node

The `resolv-contracts` bug is about **missing slippage protection (`minAmountOut`)** on a token-conversion path, letting execution occur at an arbitrary price with no bound tied to a trusted price reference. Push Chain's own architecture has an analog conversion path — the on-chain Uniswap V3 auto-swap used to convert bridged PRC20 tokens into native PC (both for gasless inbound top-ups and for excess-gas refunds) — but unlike the resolv report, this path is *not* missing slippage protection outright. Instead, the protection is derived from the **same manipulable spot AMM price** it is meant to defend against, which weakens (but does not eliminate) the guarantee.

Key code:
- `k.GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` for a live spot quote [1](#0-0) 
- `minPCOut` is derived from that same quote with a fixed 5% tolerance, then immediately used in the same call to `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`: [2](#0-1) [3](#0-2) 
- `depositPRC20WithAutoSwap` is invoked with `deadline = 0` ("contract uses its default"), i.e. no caller-supplied execution deadline is actually enforced by the module: [4](#0-3) 

The upgrade changelog confirms this was a deliberate, but partial, fix replacing an earlier "0-slippage" call: [5](#0-4) 

Because both the quote and the swap execute against the *same* live Uniswap V3 pool state within the same keeper call, an unprivileged actor who can transact against that pool immediately beforehand (in the same or an adjacent block) can move the spot price so that the computed `minPCOut` reflects the manipulated price rather than a fair market rate — the bound only guarantees "within 5% of whatever the price is right now," not "within 5% of a fair reference price." Since these pools are ordinary Uniswap V3 pools deployed for PRC20/WPC pairs, they are open to any address, meaning the pre-trade is achievable by an unprivileged external actor without validator, relayer, or admin cooperation.

This is a genuine, reportable analog, but it is materially weaker than the resolv finding (there IS a bound, just one anchored to a manipulable spot price rather than an independent oracle/TWAP), and the value extracted per swap is capped by the swap size (gas-refund amounts, or the deposited bridge amount) and by the 5% band — limiting blast radius per instance.

### Title
Slippage-protection bound for PRC20→PC auto-swaps is derived from a manipulable on-chain spot AMM quote, not an independent price reference - (File: x/uexecutor/keeper/evm.go, execute_inbound_gas.go, outbound.go)

### Summary
`CallPRC20DepositAutoSwap` (used for GAS / GAS_AND_PAYLOAD inbound funding) and `CallUniversalCoreRefundUnusedGas`'s swap leg (used for excess-gas refunds on outbound finalization) both compute `minPCOut` from `GetSwapQuote`, a live call to the Uniswap V3 `QuoterV2` against the pool's current spot state, then immediately execute the swap against that same pool with only a fixed 5% tolerance and `deadline=0`. Because the quote and the swap operate on the same manipulable AMM state, and the pool is open to any trader, an unprivileged attacker can move the pool price shortly before the module's derived swap executes, causing the recipient (a UEA owner receiving gas top-up, or the gas-refund recipient) to receive up to ~5% less PC than fair value while the attacker captures the difference via arbitrage — all while the transaction still "succeeds" because the bound is self-referential.

### Finding Description
`GetSwapQuote` reads `quoteExactInputSingle` from the on-chain Uniswap V3 Quoter using the pool's current reserves/price [1](#0-0) . Immediately after, `minPCOut` is derived as `quote * 95 / 100` and passed into the same-block swap call in both the inbound gas-abstraction path [6](#0-5)  and the gas-refund path executed on outbound vote finalization [3](#0-2) . There is no reference to a time-weighted average price, external oracle, or any value independent of the pool's instantaneous state at call time. The `deadline` parameter passed to `depositPRC20WithAutoSwap` is hardcoded to `0` rather than a caller-bound expiry [7](#0-6) , so there is also no protection against delayed execution in a stale-price context.

Since the underlying Uniswap V3 pools (PRC20/WPC) are ordinary, permissionless pools on the Push Chain EVM, any unprivileged account can submit a large swap immediately before the block/tx in which the module's `MsgVoteInbound`-quorum-triggered auto-swap or `MsgVoteOutbound`-triggered gas refund executes, shifting the spot price. The subsequent quote and its derived 5% band are computed *after* that manipulation, so the "protection" only bounds slippage relative to the attacker's own manipulated price, not a fair market price.

### Impact Explanation
This corrupts the amount of native PC actually delivered to UEA/gas-refund recipients relative to fair value — a form of value extraction from protocol users each time an inbound GAS/GAS_AND_PAYLOAD tx or an outbound excess-gas refund triggers the auto-swap path. It affects the "corruption of PRC20 or native asset accounting … refund accounting" scope item, since the delivered amount silently deviates from the fair-value amount by up to the manipulated delta, bounded loosely by the 5% band. Impact per instance is limited (bounded by swap size and 5% tolerance, with a no-swap fallback path on outright failure), so this is a value-leakage issue rather than a fund-draining or freezing bug.

### Likelihood Explanation
Exploitability requires the attacker to trade against the specific PRC20/WPC pool immediately before the module's derived swap lands in the same or an adjacent block — feasible for any user who can predict/observe pending `MsgVoteInbound`/`MsgVoteOutbound` transactions (quorum-crossing votes are visible in the mempool) and who has capital to move a possibly thin PRC20/WPC pool. Likelihood scales inversely with pool depth and directly with the attacker's ability to time transactions relative to validator vote submission, which is plausible on a live chain with public mempool visibility.

### Recommendation
Anchor `minPCOut` (and the equivalent bound for `refundUnusedGas`) to a manipulation-resistant reference, e.g., a Uniswap V3 TWAP over a meaningful window, or an independently-maintained on-chain price oracle, rather than the instantaneous `quoteExactInputSingle` result. Additionally, bind `deadline` to the actual block/context time (e.g., `ctx.BlockTime() + N`) instead of always passing `0`, so a swap cannot execute against stale/manipulated pricing after being queued.

### Proof of Concept
1. Monitor the Push Chain mempool/consensus for a `MsgVoteInbound` (GAS/GAS_AND_PAYLOAD) or `MsgVoteOutbound` transaction that is about to cross validator quorum, which will trigger `ExecuteInboundGas` → `GetSwapQuote` → `CallPRC20DepositAutoSwap`, or `applyGasRefund` → `GetSwapQuote`/`getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas(withSwap=true, ...)`.
2. Submit (or have included in the same/preceding block) a large swap against the relevant PRC20/WPC Uniswap V3 pool to move its spot price unfavorably for the upcoming module-triggered direction.
3. When the quorum-crossing vote lands, `GetSwapQuote` returns a quote computed off the now-manipulated pool state; `minPCOut = quote * 95 / 100` is derived from that manipulated quote and the swap executes successfully against it.
4. The recipient (UEA owner or gas-refund recipient) receives less native PC than fair value would dictate, while the attacker recoups the difference by reversing their manipulating trade (arbitrage) once the module's swap has executed.

### Citations

**File:** x/uexecutor/keeper/evm.go (L500-538)
```go
// GetSwapQuote calls QuoterV2.quoteExactInputSingle (commit=false) to get the expected
// output amount for swapping prc20 → wpc.
func (k Keeper) GetSwapQuote(
	ctx sdk.Context,
	quoterAddr, prc20Address, wpcAddress common.Address,
	fee, amount *big.Int,
) (*big.Int, error) {
	quoterABI, err := types.ParseUniswapQuoterV2ABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse QuoterV2 ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	params := types.AbiQuoteExactInputSingleParams{
		TokenIn:           prc20Address,
		TokenOut:          wpcAddress,
		AmountIn:          amount,
		Fee:               fee,
		SqrtPriceLimitX96: big.NewInt(0),
	}

	receipt, err := k.evmKeeper.CallEVM(ctx, quoterABI, ueModuleAccAddress, quoterAddr, false, nil, "quoteExactInputSingle", params)
	if err != nil {
		return nil, errors.Wrap(err, "QuoterV2 quoteExactInputSingle failed")
	}

	results, err := quoterABI.Methods["quoteExactInputSingle"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack quoteExactInputSingle result")
	}

	amountOut, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for amountOut: %T", results[0])
	}

	return amountOut, nil
}
```

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-153)
```go
						if execErr == nil {
							fee, execErr = k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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

**File:** x/uexecutor/keeper/outbound.go (L213-234)
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
		} else {
			swapFallbackReason = fmt.Sprintf("quote fetch failed: %s", quoteErr.Error())
		}
```

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
