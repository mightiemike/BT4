### Title
Gas-abstraction inbound swap uses same-block spot AMM quote with no TWAP protection, enabling sandwich-style price manipulation of the gas-swap output - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
The external report describes a bug class where a lending protocol accepted a price value that could be stale or manipulated relative to true market value, and an attacker exploiting the gap between price update and use could extract value or create bad debt. In Push Chain's `uexecutor` module, the `ExecuteInboundGas` flow performs an analogous "price read then act" pattern: it fetches a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote and immediately uses it (with only a fixed 5% slippage buffer) to authorize a `depositPRC20WithAutoSwap` call that mints/swaps value on behalf of the user.

### Finding Description
`ExecuteInboundGas` (x/uexecutor/keeper/execute_inbound_gas.go:103-153) resolves the fee tier via `GetDefaultFeeTierForToken`, then calls `GetSwapQuote` (x/uexecutor/keeper/evm.go:502-538), which invokes the Uniswap V3 `QuoterV2.quoteExactInputSingle` against the live pool reserves at the current block state — a spot price, not a time-weighted average. The result is used directly to compute `minPCOut = quote * 95 / 100` (a flat 5% slippage tolerance) and passed to `CallPRC20DepositAutoSwap` (x/uexecutor/keeper/evm.go:542-593), which performs the actual on-chain swap and credits the user's UEA. The identical pattern exists in `applyGasRefund`/`getSwapQuoteForRefund` (x/uexecutor/keeper/outbound.go:174-270) for outbound gas refunds.

Because the quote is read from the live pool at execution time rather than a manipulation-resistant oracle (e.g., TWAP, Chainlink, or a bounded reference price), an unprivileged actor who can influence the PRC20/WPC pool's spot price in the same block the module executes the swap (by trading against the pool directly before the module's derived EVM call lands, since inbound execution runs synchronously as part of ordinary vote-finalization block processing) can shift the quoted rate. The only protection is a flat 5% band around that already-manipulated quote, which does not prevent the attacker from moving price by more than 5% and then reversing the trade after the module's swap executes.

This mirrors the report's core class: reliance on a price value fetched without protection against short-window market manipulation, then acted upon with insufficient safety margin, converting an oracle/price-freshness gap into direct value extraction or protocol loss.

### Impact Explanation
If exploited, an attacker manipulating the pool price ahead of an `ExecuteInboundGas` or gas-refund swap can cause the module to receive fewer WPC (or PRC20) than the fair-market amount, effectively extracting value from the protocol's swap execution (the module eats the slippage while the attacker profits from the round-trip). Repeated exploitation against inbound gas-abstraction swaps or refund swaps degrades protocol-held liquidity/gas-token reserves — a form of value drain via corrupted accounting of the swap outcome credited to the UEA or refund recipient, which falls under "corruption of ... gas fee accounting, refund accounting ... token mapping" in the allowed-impact scope.

### Likelihood Explanation
Likelihood depends on whether the PRC20/WPC pool has thin liquidity and whether inbound/outbound execution is deterministically triggerable/observable ahead of time by an unprivileged actor who can also submit ordinary EVM swap transactions in the same or an adjacent block. I was not able to fully verify, within the available tool budget, the exact block-level ordering guarantees between `MsgVoteInbound` finalization (which triggers `ExecuteInboundGas`) and ordinary user transactions in the same block, nor the actual liquidity depth/configuration of the WPC pools deployed by `UniversalCore`. This limits confidence in precise exploitability and should be independently verified against the live mempool/block-execution model and pool deployment parameters.

### Recommendation
Replace the direct spot `quoteExactInputSingle` call with a manipulation-resistant reference (TWAP over a sufficient window, or a bound derived from an external price oracle) before computing `minPCOut`, and/or widen protections (e.g., cap the deviation allowed relative to a longer-window reference price, not just a flat percentage of the immediate quote). Consider also rate-limiting or batching gas-abstraction swaps to reduce the value at risk from any single manipulated quote.

### Proof of Concept
Conceptual (not fully verified against live execution ordering):
1. Attacker observes an in-flight inbound (`TxType_GAS`) approaching UV quorum, or the periodic outbound gas-refund flow, and identifies the target PRC20↔WPC pool used by `GetSwapQuote`.
2. In the same block (or immediately before) the module's `ExecuteInboundGas`/`applyGasRefund` derived EVM call executes, the attacker submits a large swap against the same pool to push the spot price away from the fair market rate.
3. The module reads the manipulated spot quote via `QuoterV2.quoteExactInputSingle`, computes `minPCOut` as 95% of that skewed quote, and executes `depositPRC20WithAutoSwap`/`refundUnusedGas` at the bad rate.
4. Attacker reverses their trade in the same/next block, capturing the price impact as profit at the module's/user's expense. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-153)
```go
					if execErr == nil {
						// --- step 4: fetch swap quote and compute minPCOut with 5% slippage
						var (
							quoterAddr common.Address
							wpcAddr    common.Address
							fee        *big.Int
							quote      *big.Int
						)

						quoterAddr, execErr = k.GetUniversalCoreQuoterAddress(sdkCtx)
						if execErr != nil {
							shouldRevert = true
							revertReason = execErr.Error()
						}

						if execErr == nil {
							wpcAddr, execErr = k.GetUniversalCoreWPCAddress(sdkCtx)
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
						}

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

**File:** x/uexecutor/keeper/evm.go (L542-593)
```go
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

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
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-270)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
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
