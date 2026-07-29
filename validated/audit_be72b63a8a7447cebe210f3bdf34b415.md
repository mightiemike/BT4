## Title
Sandwich-manipulable Uniswap V3 spot-price auto-swap in gas deposit/refund flows causes user fund loss - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report's root cause is that Uniswap V3 pricing for a swap that moves user value can be manipulated by an attacker who controls (or trades against) the pool used for the swap, because the protocol trusts a manipulable AMM-derived price with insufficient protection. Push Chain's `x/uexecutor` module has a native analog: every `GAS` and `GAS_AND_PAYLOAD` inbound (and every outbound gas refund) auto-swaps a PRC20 token to WPC using a **live spot-price quote** from Uniswap V3 `QuoterV2.quoteExactInputSingle`, protected only by a flat, hardcoded 5% slippage band with no TWAP or oracle cross-check.

### Finding Description
`GetSwapQuote` reads `quoteExactInputSingle` directly from the on-chain Uniswap V3 quoter using current pool state (`commit=false` `CallEVM`) [1](#0-0) . The resulting `quote` is immediately reduced by a fixed 5% and passed as `minPCOut` to `CallPRC20DepositAutoSwap`, which executes `depositPRC20WithAutoSwap` on `UniversalCore` [2](#0-1) . The same pattern is used for `GAS_AND_PAYLOAD` inbounds via `gasAndPayloadDepositAutoSwap` [3](#0-2) , and for excess-gas refunds on outbound finalization via `applyGasRefund`/`getSwapQuoteForRefund` [4](#0-3) .

There is no TWAP usage anywhere in the codebase (confirmed by search — no `TWAP`/`twap` hits outside these three files, and none of those hits implement a time-weighted price), and no path/pool selection to defend — the fee tier is looked up once via `defaultFeeTier[prc20]` and the single-hop `tokenIn -> WPC` swap always uses the same pool [5](#0-4) . This mirrors the H-7 bug class: a swap's output-minimum is derived from a spot AMM price that any unprivileged trader can push around within the flat 5% tolerance (or further, since 5% is the *acceptable loss band*, not a manipulation-resistance guarantee) by trading against the PRC20/WPC pool immediately before the block containing the module-triggered swap, then reversing the trade afterward (classic sandwich). Because inbound execution runs deterministically once UV vote quorum is reached — with no randomness or delay a user/attacker cannot predict — an attacker can time their manipulation trades around a known pending inbound (source-chain events and block-confirmation windows are public) exactly as described in H-7's "prepare / pre-attack pool" scenario.

### Impact Explanation
Every `GAS` / `GAS_AND_PAYLOAD` inbound deposit and every outbound gas-fee refund routes user funds through this unprotected spot-price swap. An attacker who manipulates the relevant PRC20/WPC pool's price around the execution block can force the protocol to accept a swap up to 5% worse than fair value (and if liquidity is thin, more, since nothing prevents the *quote itself* from being computed against an already-skewed pool), extracting the difference as arbitrage profit at the depositing/refunded user's expense. This is a corruption of gas-fee/refund accounting and a direct, repeatable drain of user-controlled value, matching the "corruption of ... gas fee accounting, refund accounting" and fund-loss impact categories in scope.

### Likelihood Explanation
High for any token/pool with modest liquidity: no privileged access, validator collusion, or key compromise is required. The attacker only needs capital to move the specific PRC20/WPC pool's price and standard mempool/timing awareness of when a pending inbound will reach vote quorum (source-chain block confirmations are public per `ChainConfig.BlockConfirmation`). This is purely an unprivileged, ordinary-user-reachable path (deposits triggering `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`, and successful outbounds triggering `applyGasRefund`).

### Recommendation
Replace the single spot-price `quoteExactInputSingle` check with a manipulation-resistant reference (e.g., a Uniswap V3 TWAP observation window, or cross-check against the on-chain gas-price/chain-meta oracle already maintained by `x/uexecutor`) before accepting `minPCOut`. Consider tightening slippage tolerance dynamically based on pool liquidity/observed volatility rather than a flat 5%, and/or splitting large auto-swaps to limit price-impact exposure per block.

### Proof of Concept
1. Attacker identifies a low/medium-liquidity PRC20/WPC Uniswap V3 pool used by `UniversalCore` for a given PRC20 (fee tier fixed via `defaultFeeTier[prc20]`).
2. Attacker observes a pending `GAS`/`GAS_AND_PAYLOAD` inbound (source-chain tx visible pre-confirmation) that will trigger `ExecuteInboundGas` → `GetSwapQuote` → `CallPRC20DepositAutoSwap` once UV quorum is reached [6](#0-5) .
3. In the block(s) immediately before quorum-triggered execution, attacker swaps a large amount into/out of the PRC20/WPC pool to skew the spot price unfavorably for the pending deposit.
4. `GetSwapQuote` returns a depressed quote; `minPCOut = quote * 0.95` is computed against this already-skewed price and passed to `depositPRC20WithAutoSwap`, so the swap executes at the manipulated (bad) price rather than reverting.
5. Attacker reverses their position afterward, capturing the price-impact difference as profit; the depositing user receives fewer WPC than fair value would have provided.

### Citations

**File:** x/uexecutor/keeper/evm.go (L470-498)
```go
// GetDefaultFeeTierForToken reads defaultFeeTier[prc20] from UniversalCore.
func (k Keeper) GetDefaultFeeTierForToken(ctx sdk.Context, prc20Address common.Address) (*big.Int, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "defaultFeeTier", prc20Address)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call defaultFeeTier")
	}

	results, err := abi.Methods["defaultFeeTier"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack defaultFeeTier result")
	}

	// go-ethereum unpacks uint24 as *big.Int (non-standard widths always map to *big.Int)
	fee, ok := results[0].(*big.Int)
	if !ok {
		return nil, fmt.Errorf("unexpected type for defaultFeeTier: %T", results[0])
	}

	return fee, nil
}
```

**File:** x/uexecutor/keeper/evm.go (L500-537)
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
```

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
