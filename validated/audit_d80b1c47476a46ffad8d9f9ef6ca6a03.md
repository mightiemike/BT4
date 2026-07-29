## Analysis

The external report's root cause is using a single, same-transaction, unauthenticated quote as the sole basis for accepting a swap price, letting the caller manipulate that quote to extract value. Push Chain's `x/uexecutor` has the same anti-pattern in its gas-autoswap paths.

### Where it lives

`x/uexecutor/keeper/evm.go`'s `GetSwapQuote` fetches a spot-price quote by calling the Uniswap-V3-style `QuoterV2.quoteExactInputSingle` with `commit=false` on the live pool [1](#0-0) . That quote is the *only* input used to compute `minPCOut` with a hardcoded 5% slippage tolerance, and is immediately handed to `CallPRC20DepositAutoSwap`, which performs the real, committed swap on `UniversalCore.depositPRC20WithAutoSwap` [2](#0-1) . There is no independent price oracle, no TWAP, and no cross-check against a baseline/market value — the quote and the executed swap both read the same manipulable pool state.

This exact pattern is used in three places:
- `ExecuteInboundGas` for `GAS` inbounds [3](#0-2) 
- `gasAndPayloadDepositAutoSwap` for `GAS_AND_PAYLOAD` inbounds [4](#0-3) 
- `applyGasRefund` on outbound finalization (gas-token → PC refund swap) [5](#0-4) 

An upgrade note confirms the swap-quote-based slippage check is a deliberate, recently-added "protection": *"GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2 contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%) to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call."* [6](#0-5) 

### Why this is the same bug class

In the TeaVaultAmbient report, the pool manager could get an unfavorable-rate swap accepted because the check validating the swap's output was based on data the manager itself controlled/could bypass, rather than a trustworthy independent value. Here, the "trust anchor" (`GetSwapQuote`) and the executed trade (`CallPRC20DepositAutoSwap`) both hit the *same* AMM pool in essentially the same block, with no oracle/TWAP separation. Anyone who can move that pool's spot price in the same or adjacent block (a normal, unprivileged EVM swap on Push Chain — no special privilege required) can force `minPCOut` to be computed against a manipulated price, then have `depositPRC20WithAutoSwap` execute at that manipulated rate. Because deposits are user-triggered (an attacker fully controls the timing and content of their own crosschain inbound, since UVs vote deterministically on an event the attacker manufactured), the attacker can align their own price-moving swap with the block in which their inbound's autoswap finalizes, extracting PC from `UniversalCore`'s pool at a rate divorced from fair market value — i.e., draining protocol-owned liquidity, matching the "theft of funds via manipulated swap execution" impact class from the report.

This is a plausible, reachable, unprivileged-attacker analog worth flagging, though I want to note two caveats before you rely on it: (1) I could not find the Solidity source for `UniversalCore.depositPRC20WithAutoSwap`/`refundUnusedGas` in this repo (they live in `push-chain-core-contracts`), so I can't confirm whether that contract enforces any additional protections (e.g., deadline, max price impact) beyond the `minPCOut` passed in; and (2) exact attacker profitability depends on pool depth/liquidity available on Push Chain, which I can't assess from this repo alone. [7](#0-6) 

Given the significant uncertainty around whether the external `UniversalCore` contract has compensating controls, and since this requires deeper verification of Solidity-side guards outside this repo's indexed scope, I'd recommend treating this as a candidate finding requiring confirmation rather than a fully verified vulnerability. If you want, I can help formulate the precise reproduction steps assuming worst-case (no additional contract-side guards), but flagging this caveat is important for accuracy.

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
