## Analysis Result

### Title
Gas-abstraction and gas-refund PRC20→PC swaps derive slippage protection from the same manipulable spot quote they are supposed to protect against - ([File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/outbound.go], [File: x/uexecutor/keeper/evm.go])

### Summary
The Curve report shows `calc_withdraw_one_coin` returning a manipulable on-chain spot price that is used, un-mitigated, to compute a burn amount. Push Chain has a structurally identical pattern in its Uniswap V3 gas-abstraction and gas-refund swap paths: `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` [1](#0-0)  to read the instantaneous pool price, and the resulting `minPCOut` slippage bound is derived by simply taking 95% of that same quote [2](#0-1) . There is no TWAP, no external oracle, and no independent reference price anywhere in this call chain.

### Finding Description
Three module-originated `DerivedEVMCall` flows all follow the same pattern:
- `ExecuteInboundGas` (gas-only inbound processing) fetches a quote and computes `minPCOut = quote*95/100` immediately before calling `CallPRC20DepositAutoSwap` [3](#0-2) .
- `gasAndPayloadDepositAutoSwap` (used by `ExecuteInboundGasAndPayload`) does the identical quote→minPCOut→swap sequence [4](#0-3) .
- `applyGasRefund` (outbound gas-refund path) fetches a quote via `getSwapQuoteForRefund` and computes `minPCOut` the same way before calling `CallUniversalCoreRefundUnusedGas` [5](#0-4) .

In every case, `GetSwapQuote` reads `QuoterV2.quoteExactInputSingle` against the live pool state [6](#0-5) , and the resulting `minPCOut` is a fixed percentage of that same value. Because the "protection" is a function of the exact value being manipulated, it cannot prevent — and in fact validates — a swap executed at an already-skewed price. This is the same failure mode Zokyo flagged for Curve's `calc_withdraw_one_coin`: computing an on-chain economic guard from a manipulable spot price provides no real protection against price manipulation, only against unrelated in-block slippage.

### Impact Explanation
An unprivileged attacker can move the PRC20/WPC Uniswap V3 pool price (a normal, permissionless EVM swap on Push Chain) immediately before their own cross-chain deposit is processed by validators via `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`, or before their own gas refund is processed via `applyGasRefund`. Since `minPCOut` is derived from the same skewed quote, the deposit-auto-swap or refund-swap will still "pass" its slippage check while paying out an inflated amount of native PC (WPC) from module/protocol-held liquidity, or conversely cause other users' swaps to receive a deflated amount. This directly affects PRC20/native asset accounting and gas fee/refund accounting for protocol-controlled funds, matching the "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting" and "draining... protocol-controlled funds" impact categories.

### Likelihood Explanation
The attacker only needs standard, permissionless capabilities: submit an EVM swap transaction on Push Chain to move the pool price, and trigger their own inbound deposit or accumulate excess gas refund through ordinary cross-chain deposit flows. No validator, relayer, or admin cooperation is required, and inbound/outbound execution timing is externally observable, making sequencing (manipulate pool, then have own inbound/outbound processed) practically achievable.

### Recommendation
Do not derive `minPCOut` from the same instantaneous `quoteExactInputSingle` call used to execute the swap. Use a TWAP-based price (e.g., Uniswap V3 pool `observe()`) or an external oracle as the reference price for computing the slippage bound, and/or cap the deviation between the spot quote and the TWAP/oracle reference before allowing the swap to proceed, consistent with the auditor's partial-mitigation guidance in the original report.

### Proof of Concept
1. Attacker identifies the PRC20↔WPC pool used for gas-token swaps (address returned by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`) [7](#0-6) .
2. Attacker submits a large swap in that pool to skew the instantaneous price in their favor.
3. Attacker submits (or already has pending) a cross-chain deposit that will be processed by `ExecuteInboundGas`, or accrues an over-estimated gas fee refund processed by `applyGasRefund`.
4. When the module executes the swap, `GetSwapQuote` reads the now-skewed price and `minPCOut = quote*95/100` is computed from that same skewed value [8](#0-7) , so the check trivially passes while the attacker receives an inflated PC payout from protocol-held liquidity.
5. Attacker (optionally) reverses their manipulating swap afterward to restore the pool price, retaining the inflated PC extracted from the module.

### Citations

**File:** x/uexecutor/keeper/evm.go (L422-468)
```go
// GetUniversalCoreQuoterAddress reads the uniswapV3Quoter address stored in UniversalCore.
func (k Keeper) GetUniversalCoreQuoterAddress(ctx sdk.Context) (common.Address, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "uniswapV3Quoter")
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to call uniswapV3Quoter")
	}

	results, err := abi.Methods["uniswapV3Quoter"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to unpack uniswapV3Quoter result")
	}

	return results[0].(common.Address), nil
}

// GetUniversalCoreWPCAddress reads the WPC (wrapped PC) address stored in UniversalCore.
func (k Keeper) GetUniversalCoreWPCAddress(ctx sdk.Context) (common.Address, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, abi, ueModuleAccAddress, handlerAddr, false, nil, "WPC")
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to call WPC")
	}

	results, err := abi.Methods["WPC"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return common.Address{}, errors.Wrap(err, "failed to unpack WPC result")
	}

	return results[0].(common.Address), nil
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
