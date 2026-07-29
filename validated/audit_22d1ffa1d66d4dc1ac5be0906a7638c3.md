### Title
Sandwichable auto-swap slippage protection derived from manipulable Uniswap V3 spot price (no TWAP) - ([File: x/uexecutor/keeper/evm.go])

### Summary
`x/uexecutor` executes protocol-owned swaps (PRC20 gas-token → WPC) as part of ordinary, unprivileged inbound processing (`GAS` / `GAS_AND_PAYLOAD` deposits) and outbound gas refunds. The slippage guard (`minPCOut`) is derived from `QuoterV2.quoteExactInputSingle`, which reads the pool's current spot price (the same manipulable value UniswapV3 exposes via `slot0`), with `sqrtPriceLimitX96` hardcoded to `0` (no limit). This is the same bug class as the external report: a spot-price-derived value is used both to *quote* and to *bound* a swap that executes immediately afterward in the same keeper call chain, so an attacker who moves the pool price beforehand (or sandwiches the deposit) gets a self-referential "protection" that offers no real defense.

### Finding Description
`GetSwapQuote` in [1](#0-0)  calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96: big.NewInt(0)` — i.e., unrestricted, spot-price-based quoting, with no TWAP averaging.

This quote is used immediately, in the same execution path, to compute `minPCOut = quote * 95 / 100` and pass it straight into `CallPRC20DepositAutoSwap`, which performs the actual on-chain swap: [2](#0-1) 

The identical pattern (quote → 5% slippage → swap, all back-to-back with no time separation) also appears in the `GAS_AND_PAYLOAD` inbound path: [3](#0-2) 

and in the outbound unused-gas refund path, which swaps the leftover gas token back to PC before returning it to the user: [4](#0-3) 

Because the quote and the swap read the pool's live reserves/price at essentially the same instant, any spot-price manipulation (a large swap or flashloan against the WPC/PRC20 pool immediately before the inbound/outbound is processed) shifts the quote and the resulting `minPCOut` bound in tandem with the manipulated price. The "5% slippage protection" therefore bounds against the *manipulated* price, not a fair market price — it does not stop a sandwich attack, it only limits how much *further* the attacker can push things beyond the price they've already skewed.

This is functionally identical to the external report's root cause: using `slot0`-derived (spot) pricing instead of a TWAP for a value that gates fund movement, in a UniswapV3 QuoterV2 integration.

### Impact Explanation
The swapped funds are protocol-owned liquidity interactions triggered by ordinary user deposits and refunds — no privileged actor is required. An attacker can sandwich the module's auto-swap (front-run by skewing the PRC20/WPC pool price, let the module's deposit-triggered swap execute at the skewed price bounded only by a 95%-of-skewed-quote floor, then back-run to restore price and capture the value difference). This can:
- reduce the amount of native PC minted to the depositing user (loss for the end user), and/or
- extract value from whichever side of the pool the module's swap executes against, at the expense of the pool/protocol-facing liquidity used for `depositPRC20WithAutoSwap` / `refundUnusedGas`.

This falls under "corruption of PRC20 or native asset accounting... must not misroute value" and "unauthorized ... unauthorized refund of user or protocol-controlled funds" in the allowed-impact gate, reachable purely from ordinary unprivileged deposit/withdraw flows with honest validators and honest nodes.

### Likelihood Explanation
High. Any user can trigger the vulnerable code path simply by depositing a `GAS` or `GAS_AND_PAYLOAD` inbound (or by triggering an outbound gas refund), which are the default, most common transaction types in the protocol. The attacker only needs the ability to trade against the same on-chain Uniswap V3 pool the module uses — no special permissions, no validator/relayer collusion, and no protocol misconfiguration are required. Sandwich/flashloan attacks against AMM spot prices are a well-known, low-cost, frequently exploited technique.

### Recommendation
Replace the spot-price-based `quoteExactInputSingle` call (and the `sqrtPriceLimitX96 = 0` unrestricted quote) with a TWAP-derived reference price (e.g., via the pool's `observe`/cumulative-tick oracle) for computing `minPCOut`, or otherwise decouple the slippage bound from a value that can be manipulated in the same block/transaction as the swap it protects. Consider also enforcing a maximum allowed deviation between the TWAP and the instantaneous quote, and/or setting a non-zero `sqrtPriceLimitX96` bound.

### Proof of Concept
1. Attacker identifies the WPC/PRC20 pool used by `UniversalCore` for a given gas token (via `defaultFeeTier` / quoter address in `x/uexecutor/types/abi.go`).
2. Attacker submits a large swap (optionally flashloan-funded) against that pool to skew its spot price just before submitting (or racing) a `GAS`/`GAS_AND_PAYLOAD` inbound deposit.
3. When Universal Validators finalize the inbound ballot and `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` runs, `GetSwapQuote` ( [1](#0-0) ) returns a quote based on the now-skewed spot price; `minPCOut` is computed as 95% of that skewed quote and passed to `CallPRC20DepositAutoSwap`.
4. The swap executes at the skewed price, bounded only by the self-referential `minPCOut`.
5. Attacker reverses their initial swap, capturing the price-impact difference at the expense of the pool/protocol and/or the depositing user's expected PC output.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-379)
```go
	fee, err := k.GetDefaultFeeTierForToken(sdkCtx, prc20AddressHex)
	if err != nil {
		return nil, err
	}

	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
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
