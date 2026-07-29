## Analysis

The AJNA bug is fundamentally about a **value-conversion mechanism whose protective price bound is derived from the mechanism itself rather than from an external floor tied to the underlying value**, letting an opportunistic actor extract the difference. The closest reachable analog in Push Chain's scoped code is the gasless PRC20→PC auto-swap path used on `GAS` / `GAS_AND_PAYLOAD` inbounds and on unused-gas refunds, where `minPCOut` slippage protection is derived solely from a same-block spot quote off Push Chain's own Uniswap V3 pool rather than an external reference price. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Auto-swap deposits/refunds derive slippage floor from a manipulable same-block AMM spot quote instead of an external value reference - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`GAS` and `GAS_AND_PAYLOAD` inbounds, and the excess-gas refund path on outbounds, mint the user's bridged PRC20 and immediately swap it to native PC via `depositPRC20WithAutoSwap` / `refundUnusedGas`. The only slippage protection is `minPCOut = quote * 95 / 100`, where `quote` comes from `GetSwapQuote` calling Uniswap V3 `QuoterV2.quoteExactInputSingle` — an instantaneous spot-price read of the on-chain PRC20/WPC pool [1](#0-0) . This mirrors the AJNA analog: the value floor used to protect the counter-party is computed from the very mechanism being manipulated (auction price / spot AMM price), not from an independent reference of what the asset is actually worth, so it "can fall through the floor" of real value.

### Finding Description
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` fetch `fee` via `GetDefaultFeeTierForToken`, fetch `quote` via `GetSwapQuote` against the live pool, compute `minPCOut` as 95% of that quote, and call `CallPRC20DepositAutoSwap` in the same keeper invocation [2](#0-1) [4](#0-3) . `applyGasRefund` does the identical pattern for the unused-gas refund leg [3](#0-2) .

Because `quoteExactInputSingle` reads the pool's current tick/liquidity state rather than a time-weighted price, an unprivileged user can submit an ordinary large swap against the same WPC/PRC20 pool on Push Chain in a transaction landing immediately before the module's inbound-finalization transaction (inbound execution happens synchronously inside `MsgVoteInbound` once quorum is reached, at a block height the attacker can anticipate by watching the mempool/vote count). This pushes the spot price down, so the honest depositor's auto-swap executes against the manipulated pool and only needs to clear the depressed `minPCOut` floor — the attacker then reverses their manipulating trade in a following transaction, capturing the price impact extracted from the victim's deposit. The "floor" that is supposed to protect the user's converted value never anchors to anything external to the manipulable pool itself, exactly as the AJNA auction price had no floor tied to the loan's real value.

### Impact Explanation
An unprivileged attacker can extract value from every PRC20-to-PC auto-swap conversion (inbound gas top-ups, gas-and-payload deposits, and outbound excess-gas refunds) by sandwiching the pool used for `quoteExactInputSingle`. This corrupts PRC20/native asset accounting for legitimate depositors — they receive less PC than the deposited asset is worth, with no accounting safeguard beyond a flat 5% band computed off the same manipulated price. This falls within scope under "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting... or canonical UniversalTx state" and "unauthorized module-originated EVM execution."

### Likelihood Explanation
Medium: it requires the attacker to control pool liquidity/price impact relative to pool depth and to time an ordinary transaction ahead of the module's own swap execution. On a low-liquidity WPC/PRC20 pool (plausible for newly-listed PRC20 gas tokens) this is straightforward and repeatable, since inbound finalization timing is observable (quorum-triggered) and the flat 5% slippage band is generous enough to absorb non-trivial manipulation.

### Recommendation
Do not derive `minPCOut` purely from an instantaneous same-pool spot quote. Use a TWAP-based Uniswap V3 quote (or an external price reference/oracle) for the floor, and/or bound the acceptable slippage against a recently observed chain-meta/oracle price rather than a fixed percentage of the manipulable quote itself, so the swap floor cannot be pushed below the asset's real value by an unprivileged actor manipulating the same block's pool state.

### Proof of Concept
1. Attacker identifies a PRC20 gas-token/WPC Uniswap V3 pool used by `UniversalCore` with shallow liquidity.
2. Attacker observes a pending `MsgVoteInbound` reaching quorum for a `GAS_AND_PAYLOAD` inbound from a victim.
3. Attacker submits an ordinary large swap depressing the PRC20/WPC spot price just before the quorum-triggering vote lands.
4. Victim's inbound executes: `GetSwapQuote` returns a depressed `quote`; `minPCOut = quote*95/100` is trivially satisfied; `CallPRC20DepositAutoSwap` executes at the manipulated price, minting far less PC to the victim's UEA than the deposit is worth [5](#0-4) .
5. Attacker reverses their swap in a subsequent transaction, recapturing the price impact plus the value extracted from the victim's conversion.

### Citations

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
