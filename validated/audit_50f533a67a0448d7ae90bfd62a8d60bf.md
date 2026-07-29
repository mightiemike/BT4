### Title
Single-block spot AMM quote used for `minPCOut` slippage protection enables sandwich-extraction of user auto-swap deposits and gas refunds - (File: `x/uexecutor/keeper/evm.go`)

### Summary
The external report's root cause is a dangerous, unvalidated 1:1 price assumption used to compute exchange rates for value-bearing conversions between two economically distinct assets, letting an attacker exploit a temporary price divergence to extract more value than deposited. The Push Chain analog is structurally similar: whenever `x/uexecutor` needs to price a PRC20-token-to-native-PC conversion (auto-swap deposits on `GAS`/`GAS_AND_PAYLOAD` inbounds, and excess-gas refunds), it treats the current spot output of `QuoterV2.quoteExactInputSingle` — a single, manipulable AMM read taken in the same call sequence as the swap itself — as ground truth, protected only by a fixed 5% slippage band, with no TWAP, external oracle, or manipulation-resistance check.

### Finding Description
`Keeper.GetSwapQuote` (`x/uexecutor/keeper/evm.go`) performs a static call to `quoteExactInputSingle` on the configured Uniswap V3 `QuoterV2` to price a `prc20 → WPC` swap: [1](#0-0) 

The keeper then derives `minPCOut` as a flat 5% discount off that single quote and immediately executes the swap-backed deposit using that bound, in both the `GAS`/`GAS_AND_PAYLOAD` inbound execution path: [2](#0-1) 

and in the excess-gas-fee refund path (`applyGasRefund` / `getSwapQuoteForRefund`): [3](#0-2) 

There is no TWAP, no oracle cross-check, and no minimum-liquidity/price-staleness guard between the quote read and the swap execution — the code assumes the instantaneous AMM spot price is a faithful, un-manipulable representation of true value, exactly the same class of unvalidated price assumption the external report flags (there, a hardcoded 1:1 peg; here, an unprotected single-sample spot price with only a fixed slippage cushion). Because `depositPRC20WithAutoSwap` executes against a Uniswap V3 pool whose price an unprivileged actor can move with ordinary swap transactions, and because the quote-then-swap sequence happens within reach of ordinary transaction ordering in the same or adjacent blocks, an attacker can move the pool price beyond 5% before the deposit's swap executes and profit at the depositing user's expense (classic sandwich pattern), or push the price the other way to force the deposit's swap to revert/fail with a worse outcome for the honest user.

### Impact Explanation
A successful sandwich against the auto-swap path directly misprices the user's PRC20 → native-PC conversion during `GAS`/`GAS_AND_PAYLOAD` inbound execution or during `refundUnusedGas`, causing the depositing user (or refund recipient) to receive materially less native PC than the token's fair value while the attacker extracts the difference. This is a fund-drain/value-extraction vector against ordinary user deposits and refunds, matching the "corruption of ... gas token accounting / refund accounting" and "unauthorized ... module-originated EVM execution" impact categories in scope, since the module-originated `depositPRC20WithAutoSwap`/`refundUnusedGas` calls end up moving value at an attacker-manipulated rate rather than the honest market rate.

### Likelihood Explanation
The attack requires no validator collusion, no privileged role, and no external-chain compromise — only the ability to submit ordinary swap transactions against the same on-chain Uniswap V3 pool used by `UniversalCore`, timed around observable inbound-vote-quorum or outbound-observation transactions (which are visible in the mempool/blocks before execution). The only mitigating factor is the fixed 5% slippage band, which bounds — but does not eliminate — the extractable value per attack, and pools with thin liquidity or larger deposit amounts make the 5% band easy to exceed.

### Recommendation
- Do not rely solely on a single in-block spot quote from `QuoterV2` for `minPCOut`. Incorporate a manipulation-resistant reference (e.g., TWAP over multiple blocks, or a cross-checked external price feed) and validate the spot quote against it before accepting the swap.
- Consider tightening or dynamically sizing the slippage tolerance based on trade size relative to pool liquidity, and add a staleness/liquidity-depth check before trusting the quote.
- Where feasible, execute the quote-and-swap as atomically as possible relative to the price observation to minimize the window in which the pool can be manipulated.

### Proof of Concept
1. Attacker observes an in-flight `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will trigger `ExecuteInboundGas` / `applyGasRefund` for a sizable PRC20 amount once quorum is reached.
2. Before that transaction lands, attacker submits a swap on the `prc20/WPC` Uniswap V3 pool that moves the spot price down by just under the module's tolerance (i.e., such that the resulting `GetSwapQuote` output, discounted 5% via `minPCOut := quote*95/100`, still clears the pool's post-manipulation price).
3. The quorum-triggering transaction executes `GetSwapQuote` (see `x/uexecutor/keeper/evm.go:500-538`) against the manipulated pool state, computes a `minPCOut` based on the depressed price, and calls `CallPRC20DepositAutoSwap`, delivering the user (or refund recipient) less native PC than the token's fair value.
4. Attacker submits a reverse swap immediately after, restoring the pool price and capturing the price-impact difference as profit, funded by the value the honest depositor/refund-recipient should have received.

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

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}
```
