No TWAP or independent oracle is used anywhere else in the codebase — confirming the spot-price quote is the sole and self-referential basis for the slippage guard. This is a valid native analog to the reported bug class (an unprivileged, transiently-manipulable on-chain value used to compute a critical financial parameter that the protocol then trusts).

### Title
Self-referential AMM spot-price slippage guard allows sandwich-extraction of protocol-controlled swap value - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report describes a reward calculation that trusts an attacker-inflatable, same-transaction balance value (`receiptToken.balanceOf(user)`) with no protection against transient manipulation. The equivalent native pattern in Push Chain is `k.GetSwapQuote` [1](#0-0) , which reads the *instantaneous* spot price from the on-chain Uniswap V3 `QuoterV2.quoteExactInputSingle` and uses that same manipulable value to derive `minPCOut`, the slippage bound that is supposed to protect the very same swap:

```go
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
``` [2](#0-1) 

This same self-referential pattern is duplicated for `GAS_AND_PAYLOAD` inbounds [3](#0-2)  and for outbound gas refunds via `applyGasRefund`/`getSwapQuoteForRefund` [4](#0-3) .

### Finding Description
`GetSwapQuote` calls `quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (no price limit) and no historical/TWAP averaging window [1](#0-0) . The returned `amountOut` reflects whatever the pool's reserves are *at that exact moment*. The keeper then derives `minPCOut` directly from this same number with a flat 5% haircut and immediately calls `depositPRC20WithAutoSwap`/`refundUnusedGas`, which perform the actual swap against the same pool and check `amountOut >= minPCOut` on-chain.

Because the "protection" bound is computed from the identical, unmanipulation-resistant spot price the swap itself will execute against, the guard cannot detect or reject a price that has been pushed away from its fair value — it will always be satisfied by construction (barring intra-call price movement, which doesn't exist here since quote and swap happen in the same keeper call with no other transaction able to interleave). An unprivileged actor who moves the pool's spot price *before* this call executes (e.g., by front-running the validator-quorum-triggering `MsgVoteInbound`/`MsgVoteOutbound` transaction with a large swap, funded even via a flash loan against the pool itself, then reversing the swap afterward) forces the protocol's auto-swap or gas-refund deposit to execute at the manipulated, unfavorable price while still nominally "passing" its own slippage check. The value difference between fair price and manipulated price is extracted by the attacker.

This mirrors the reported bug's core flaw: trusting a value that is legitimately attacker-controllable within the surrounding transaction/block window as the sole input to both the executed amount and its own safety bound.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting, ... token mapping" and "unauthorized ... refund" impact categories: the protocol-controlled PC/PRC20 output from `depositPRC20WithAutoSwap` (funding a user's UEA on GAS/GAS_AND_PAYLOAD inbound) and from `refundUnusedGas` (outbound excess-gas refunds) can be routed at attacker-manipulated prices, causing the module/protocol-owned swap to yield less value than fair price while the corrupted `minPCOut` guard incorrectly reports success. This is a fund-value-drain vector reachable purely from ordinary inbound/outbound flows plus a public DEX interaction, with no privileged role required.

### Likelihood Explanation
Reachable by any unprivileged actor with capital (or flash-loan access) to move the specific Uniswap V3 pool used for `GetOutboundTxGasAndFees`/gas-token pairs. It requires timing a manipulation transaction around the validator-vote-triggered execution, which is feasible via mempool front-running or same-block MEV positioning, especially since GAS/GAS_AND_PAYLOAD inbound execution timing is predictable once quorum votes are pending.

### Recommendation
- **Short term:** Require a maximum-deviation check between the spot quote and a separately-sourced reference (e.g., a TWAP over N blocks/seconds from the same Uniswap V3 pool via `observe()`), rejecting or reverting to no-swap fallback when they diverge beyond a safe threshold.
- **Long term:** Do not derive slippage bounds from the same instantaneous call that will execute the trade; use `quoteExactInputSingle`'s `sqrtPriceLimitX96` alongside a TWAP-derived bound, and consider capping per-block swap size or requiring social/governance-configured static bounds for protocol-only swaps that aren't exposed to end-user price discretion anyway.

### Proof of Concept
1. Attacker identifies the PRC20/WPC Uniswap V3 pool used by `GetSwapQuote` for a given gas token or bridged asset.
2. Attacker (optionally via flash loan) executes a large swap against that pool to push the spot price far from fair value.
3. Attacker (or any user) submits/triggers a `FUNDS_AND_PAYLOAD`/`GAS`/`GAS_AND_PAYLOAD` inbound, or waits for outbound gas-refund processing, such that validators' `MsgVoteInbound`/`MsgVoteOutbound` execute `ExecuteInboundGas`/`applyGasRefund` while the price is still manipulated.
4. `GetSwapQuote` returns the manipulated `amountOut`; `minPCOut = amountOut * 95/100` is computed from that same manipulated number.
5. `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap at the manipulated price; the on-chain `amountOut >= minPCOut` check trivially passes since both derive from the same corrupted price.
6. Attacker reverses their initial swap, restoring the pool to fair price and pocketing the difference extracted from the protocol-controlled swap.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-152)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
