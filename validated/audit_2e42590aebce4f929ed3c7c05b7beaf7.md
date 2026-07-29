## Analysis

The external report's bug class (MEV/sandwich attacks enabled by an unprotected `amountOutMin`/slippage parameter on a DEX swap) has a direct native analog in Push Chain's `x/uexecutor` module, in the on-chain "auto-swap" logic used for inbound gas-token deposits and unused-gas refunds.

### Title
Slippage protection for module-originated PRC20→WPC auto-swaps is derived from a manipulable same-block spot price, not a TWAP or externally-verified reference - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
When Push Chain executes a `GAS` or `GAS_AND_PAYLOAD` inbound (or refunds unused relayer gas), it swaps the deposited PRC20 gas token into native PC via `UniversalCore.depositPRC20WithAutoSwap` / `refundUnusedGas`. The minimum acceptable output (`minPCOut`) is computed on-chain as 95% of a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote fetched immediately before the swap [1](#0-0) . Because the "reference price" and the "protected" swap both read the same manipulable pool state, this is functionally equivalent to a zero/near-zero `amountOutMin`: any attacker who has skewed the pool price before the module's swap executes causes the quote itself to reflect the skewed price, so the 5% tolerance band moves with the manipulation and provides no protection against sandwich-style value extraction.

### Finding Description
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` fetch the swap quote via `k.GetSwapQuote(...)`, which calls `quoteExactInputSingle` with `SqrtPriceLimitX96 = big.NewInt(0)` (no limit) [2](#0-1) , then computes `minPCOut = quote * 95 / 100` and immediately calls `CallPRC20DepositAutoSwap` with that bound [3](#0-2) . The identical pattern is used in `gasAndPayloadDepositAutoSwap` [4](#0-3)  and in the excess-gas refund path `applyGasRefund` / `getSwapQuoteForRefund` [5](#0-4) .

Both the quote fetch and the swap execution are module-originated EVM calls (`DerivedEVMCall`) triggered synchronously during inbound-ballot finalization or outbound-ballot finalization — i.e., whenever a quorum of validators' `MsgVoteInbound`/`MsgVoteOutbound` lands in a block. An unprivileged attacker can:
1. Monitor the mempool/blocks for the validator vote transaction that will cross quorum for a pending inbound (or outbound) carrying a gas-token swap.
2. Submit an ordinary swap transaction on the same Uniswap V3 pool (`prc20AddressHex -> WPC`, whose address and default fee tier are both public/queryable via `GetUniversalCoreWPCAddress`/`GetDefaultFeeTierForToken`) to push the pool price so that WPC becomes artificially cheap relative to the gas token, timed to land in the same block before the quorum-crossing vote is processed.
3. Because `GetSwapQuote` executes strictly after this manipulation, the "protective" `minPCOut` is computed off the already-skewed price, so the module's swap still executes and clears the check, receiving far less WPC than fair market value.
4. The attacker then reverses the price-moving trade in a back-run transaction in the same or next block, capturing the value that would otherwise have accrued to the UEA (in `ExecuteInboundGas`) or to the outbound sender/relayer refund recipient (in `applyGasRefund`).

This mirrors the report's root cause precisely: an on-chain swap with a "protective" bound that is derived from the very AMM state the attacker controls, giving no real protection against sandwiching — the DeFi report's fix recommendation (do not trust a manipulable, momentarily-read price; use bounded/verified pricing) is not honored here since no TWAP, external oracle, or price-impact cap independent of the instantaneous pool state is used.

### Impact Explanation
Successful exploitation directly corrupts native-asset/PRC20 accounting and gas-fee/refund accounting invariants that are explicitly in scope: the UEA that should receive `depositPRC20WithAutoSwap` proceeds, or the sender/fund-recipient that should receive `refundUnusedGas` proceeds, ends up receiving less native PC than the fair-market swap would have produced, with the difference extracted by the attacker. This is a value-drain from ordinary users' deposits and gas refunds reachable purely through default inbound/outbound transaction submission paths, without needing any privileged validator, relayer, or TSS role — satisfying the "corruption of ... gas fee accounting, refund accounting ... or unauthorized ... release ... of user or protocol-controlled funds" impact category.

### Likelihood Explanation
Likelihood depends on the liquidity depth of the specific PRC20/WPC Uniswap V3 pool used by `UniversalCore` and on how consistently an attacker can land a manipulation transaction in the same block window as the quorum-crossing validator vote. For low-liquidity gas-token pools (which is the common case for newly onboarded external gas tokens), the cost to move price beyond the 5% tolerance band is low, making this practically exploitable by any user watching the mempool. For deep/liquid pools the attack becomes costlier, reducing but not eliminating likelihood.

### Recommendation
- Do not derive `minPCOut` solely from an instantaneous `quoteExactInputSingle` call made immediately before the swap. Use a time-weighted average price (TWAP) from the pool, or an external, attacker-independent price reference, to bound acceptable output.
- Cap the maximum allowed deviation between the quote used for `minPCOut` and a longer-window reference price, rejecting/deferring the swap if the instantaneous price has moved beyond a safe bound versus the reference.
- Consider using `sqrtPriceLimitX96` on the swap itself (rather than `0`/unlimited) derived from the reference price, so the swap itself reverts if pool state has been manipulated beyond tolerance, instead of relying only on a post-hoc `amountOut` check computed from the same manipulated state.

### Proof of Concept
1. Identify a `GAS` or `GAS_AND_PAYLOAD` inbound (or a pending outbound eligible for excess-gas refund) awaiting quorum, whose associated PRC20 gas token has a shallow Uniswap V3 pool against WPC (fetched via `GetUniversalCoreWPCAddress`/`GetDefaultFeeTierForToken`, both public reads).
2. Submit a large ordinary swap transaction on that pool to depress the WPC price relative to the gas token, timed for the same block as the validator's quorum-crossing `MsgVoteInbound`/`MsgVoteOutbound`.
3. When quorum is reached, `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund` call `GetSwapQuote` against the now-skewed pool and compute `minPCOut = quote*95/100` from that skewed quote [6](#0-5) , then execute `depositPRC20WithAutoSwap`/`refundUnusedGas`, which clears the (skewed) bound but yields less WPC/native PC than fair value.
4. Submit a back-run transaction reversing the price manipulation, realizing the arbitrage profit extracted from the module's swap.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-379)
```go
// gasAndPayloadDepositAutoSwap handles the swap quote + deposit autoswap for GAS_AND_PAYLOAD.
func (k Keeper) gasAndPayloadDepositAutoSwap(
	sdkCtx sdk.Context,
	prc20AddressHex common.Address,
	ueaAddr common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

	wpcAddr, err := k.GetUniversalCoreWPCAddress(sdkCtx)
	if err != nil {
		return nil, err
	}

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

**File:** x/uexecutor/keeper/outbound.go (L213-270)
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

// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
}
```
