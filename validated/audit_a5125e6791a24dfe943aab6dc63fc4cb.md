### Title
Slippage protection for PRC20 auto-swap and gas-refund flows is derived from a same-block spot quote, not a TWAP, enabling sandwich extraction of user deposit value — (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every PRC20→PC auto-swap path in `x/uexecutor` (inbound gas swap, inbound gas+payload swap, and outbound gas-refund swap) computes its slippage floor (`minPCOut`) from `GetSwapQuote`, which is a live `QuoterV2.quoteExactInputSingle` call against the Uniswap V3 pool's *current* reserves in the same block/transaction the swap itself executes in. There is no time-weighted reference price. This is the same class of bug as the reported `UniswapPriceOracle.validatePrice()` issue: the "protective" bound is mathematically derivable from — and equal to — the manipulable spot price it is meant to guard against, so it provides no real defense against same-block price manipulation.

### Finding Description
`GetSwapQuote` in [1](#0-0)  calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` with `commit=false`, returning the current instantaneous swap output for the pool's live reserves.

That quote is then used directly to compute the slippage bound with a fixed 5% tolerance: [2](#0-1) 
```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```
The same pattern repeats verbatim in `gasAndPayloadDepositAutoSwap` [3](#0-2)  and in the gas-refund swap path `applyGasRefund`/`getSwapQuoteForRefund` [4](#0-3) .

Because the quote and the swap execution both read the same pool state within the same processing window (inbound execution happens deterministically as part of ordinary block/tx processing once a ballot finalizes), an unprivileged attacker who can get transactions ordered around the module-originated swap (e.g., by paying a higher gas price to land immediately before it, and a follow-up tx immediately after) can:
1. Push the Uniswap V3 pool price against the protocol/user right before `GetSwapQuote` is read.
2. Let the deposit auto-swap or gas-refund swap execute at the manipulated quote — the `minPCOut` floor is only 5% below that same manipulated number, so it does not stop the swap.
3. Reverse the manipulation afterward, capturing the difference between the true (unmanipulated) price and the manipulated execution price.

This is structurally identical to the reported defect: a "slippage guard" that is computed from the same spot state it is supposed to check against collapses to a no-op guard against same-block manipulation, differing from the report only in that no TWAP is attempted at all (whereas the report's bug pretends to compute one and algebraically degenerates to spot).

### Impact Explanation
The victims are ordinary users bridging funds into Push Chain (their PRC20 deposit is auto-swapped to native PC to pay gas) and outbound gas-refund recipients. A sandwich attacker can extract value from these swaps at the expense of the depositing user/protocol, corrupting the expected PRC20/native accounting for that inbound or refund. This falls under "corruption of PRC20 or native asset accounting" / "unauthorized... refund... of user or protocol-controlled funds" in the allowed impact set, reachable purely from ordinary deposit/inbound flows plus attacker-controlled DEX trades — no privileged actor required.

### Likelihood Explanation
Exploitation requires only: (a) observing an inbound ballot approaching finalization or an outbound awaiting gas refund (both are publicly observable on-chain state/mempool), and (b) submitting ordinary swap transactions against the same Uniswap V3 pool with higher gas price to land around the module's derived swap. No governance, validator, or TSS privilege is needed. The fixed 5% tolerance bounds the damage per swap but does not eliminate it, and is trivially profitable for pools with material liquidity depth relative to the swap size, or for low-liquidity PRC20/WPC pools where a modest sandwich can move price by more than 5%.

### Recommendation
Replace the same-block spot quote with a genuine time-weighted reference (e.g., a Uniswap V3 TWAP via `OBSERVE`/`increaseObservationCardinalityNext`, or an external validated oracle price) before computing `minPCOut`, and/or tighten slippage bounds dynamically based on pool depth. At minimum, do not derive both the reference price and the enforcement bound from the identical instantaneous quoter call within the same execution.

### Proof of Concept
1. Monitor pending `MsgVoteInbound` transactions that will finalize an inbound ballot for a PRC20 deposit routed through `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`.
2. Submit a large swap on the relevant PRC20/WPC Uniswap V3 pool with elevated gas price to land immediately before the finalizing vote tx is processed, shifting the pool price unfavorably for the upcoming auto-swap direction.
3. The finalizing tx triggers `GetSwapQuote` → `quote` reflects the manipulated pool state; `minPCOut = quote * 95/100` is computed from that same manipulated state and does not block execution.
4. `CallPRC20DepositAutoSwap` executes at the manipulated price, producing less PC output than a fair TWAP-based quote would allow.
5. Submit a reverse swap afterward to restore the pool price and realize the arbitrage profit extracted from the victim deposit/refund.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
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
```

**File:** x/uexecutor/keeper/outbound.go (L213-269)
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
```
