This confirms the pattern: `GetSwapQuote` reads a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot-price call [1](#0-0) , and the resulting `minPCOut` is computed as a fixed 5% haircut off that same spot quote immediately before the module-originated swap executes [2](#0-1) . There is no TWAP, independent oracle, or price-impact cap anywhere in the module — the only slippage matches found repo-wide are chain-meta gas-price oracles for external chains, unrelated to this AMM quote path.

### Title
Self-referential spot-price quoting in `x/uexecutor` gas-abstraction auto-swap allows unprivileged sandwich extraction of user deposit value - (File: x/uexecutor/keeper/execute_inbound_gas.go, execute_inbound_gas_and_payload.go, evm.go, outbound.go)

### Summary
When Push Chain executes a `GAS`, `GAS_AND_PAYLOAD` inbound, or refunds unused gas on an outbound, the module converts the user's bridged PRC20 into native PC by calling `depositPRC20WithAutoSwap` on the on-chain UniversalCore Uniswap-V3-style pool [3](#0-2) . The minimum acceptable output (`minPCOut`) is derived from a live spot-price quote fetched via `GetSwapQuote` (`QuoterV2.quoteExactInputSingle`, `commit=false`) obtained just before the swap itself, with a fixed 5% band [4](#0-3) . The same self-referential pattern is repeated in `gasAndPayloadDepositAutoSwap` [5](#0-4)  and in the excess-gas refund path `applyGasRefund`/`getSwapQuoteForRefund` [6](#0-5) .

### Finding Description
This is the same root-cause pattern as the reported `DstSwapper.processTx` issue: the protocol trusts a price reference that is itself manipulable and checked at execution time with no independent value anchor, rather than bounding the swap against a trusted external valuation. Here, instead of a keeper-supplied `txData`, the manipulable input is the live AMM pool state that any unprivileged trader can move by transacting against the same UniversalCore pool. Because the quote and the swap execution both read from the same mutable on-chain pool state, and the check is simply "did we get at least 95% of what the pool says right now," an attacker who first pushes the pool price down (e.g., large sell against WPC/PRC20 pair, or waiting for/inducing thin liquidity), then lets the protocol's auto-swap execute against the depressed price, and finally restores the price, extracts the difference from the swapped user funds — the 5% band offers no protection because it is computed from the manipulated price itself, not from an outside reference. Unlike a user-initiated swap (where the user sets their own minOut based on their own expectations), here the "depositor" (an inbound bridge user or refund recipient) never supplies or reviews the slippage parameter at all — it is entirely computed by the protocol from a spot price with no time-weighting or oracle cross-check.

### Impact Explanation
Funds converted through the auto-swap belong to the bridging user (their deposited PRC20 destined to become gas-covering PC) or to the outbound sender (excess gas refund). A successful sandwich directly reduces the amount of native PC credited to the user's UEA / recipient versus fair value, i.e. value is siphoned from user/protocol-controlled funds during a standard, unprivileged deposit or refund flow — matching the in-scope "stealing ... funds" and "corruption of ... gas fee accounting, refund accounting" impact categories.

### Likelihood Explanation
This requires only ordinary, permissionless trading against the UniversalCore Uniswap-style pool — no validator, keeper, or admin privilege is needed. The attacker needs visibility into pending/likely inbound gas-abstraction deposits (observable from source-chain gateway events before Push Chain finalizes the ballot, giving a multi-block window) and sufficient capital relative to pool depth to move price beyond 5%, which is plausible for newly listed or thinly-liquidity PRC20/WPC pairs.

### Recommendation
Do not derive `minPCOut` solely from a spot quote taken immediately before the swap. Use a time-weighted average price (TWAP) from the pool, or an independent oracle price feed, to bound the acceptable swap output, and/or cap the swap size relative to available pool liquidity to limit price-impact exposure, similar to the external report's recommendation of anchoring against an oracle-derived expected value with bounded deviation rather than self-referential quoting.

### Proof of Concept
1. Attacker monitors a source chain (e.g. Ethereum) gateway for a `GAS` or `GAS_AND_PAYLOAD` deposit event using PRC20 token `X` with a low-liquidity `X/WPC` pool on UniversalCore.
2. Before Universal Validators finalize the inbound ballot (a multi-block window), attacker submits a large sell of `X` into the pool on Push Chain, depressing the `X→WPC` price.
3. When quorum is reached, `ExecuteInboundGas` calls `GetSwapQuote` [7](#0-6)  against the depressed pool, computes `minPCOut = quote * 95/100` [8](#0-7) , and executes `CallPRC20DepositAutoSwap`, converting the user's PRC20 at the manipulated rate.
4. Attacker reverses their initial trade, restoring the pool price and pocketing the spread, which was extracted from the user's deposit value.

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

**File:** x/uexecutor/keeper/evm.go (L540-592)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
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
