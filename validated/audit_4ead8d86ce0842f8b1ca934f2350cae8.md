### Title
Uniswap V3 spot-quote used for gas-abstraction autoswap allows sandwich/flashloan extraction of user deposit value - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
When a `GAS` or `GAS_AND_PAYLOAD` inbound is finalized (and again when refunding unused outbound gas), the `uexecutor` module derives `minPCOut` for the mandatory PRC20→WPC autoswap directly from a single-block, single-source `QuoterV2.quoteExactInputSingle` spot read on Push Chain's own Uniswap V3 pool, with a flat 5% slippage tolerance and no TWAP/multi-source check — the same architectural flaw as the reported `PriceProvider` bug (single-DEX, spot-price, flashloan/sandwich-manipulable price used to gate a fund-moving action).

### Finding Description
`ExecuteInboundGas` (and `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`'s refund-swap path) all follow this pattern: [1](#0-0) 

1. `GetSwapQuote` performs a `commit=false` static call to `QuoterV2.quoteExactInputSingle` at current chain state: [2](#0-1) 
2. `minPCOut` is computed as `quote * 95 / 100` — a fixed 5% band around whatever the pool's instantaneous price happens to be at the moment of quoting: [3](#0-2) 
3. The actual swap is then committed via `CallPRC20DepositAutoSwap` inside the same module-originated `DerivedEVMCall`: [4](#0-3) 

The same quote→95%→swap pattern is reused for the outbound gas-refund swap path: [5](#0-4) [6](#0-5) 

Because the quote and the swap happen back-to-back within the same keeper call (no cross-block delay, no TWAP window), an unprivileged attacker who can move the PRC20/WPC pool price on Push Chain (any user can trade against this AMM permissionlessly) can push the pool to an unfavorable price immediately before the module reads the quote, causing the module's autoswap to execute at that depressed price. The 5% band only bounds *additional* slippage during the module's own swap — it does nothing to prevent the *quote itself* from being pre-manipulated. An attacker can:
- Sell a large amount of WPC/PC into the pool (dump) right before the inbound is finalized, dragging `quoteExactInputSingle` output down.
- Let the module's autoswap execute against the depressed pool, delivering the depositing user's UEA far less WPC/PC than fair value (only bounded by the 5% floor derived from the *already-manipulated* price, not fair value).
- Reverse the trade afterward, capturing the value extracted from the victim's deposit as arbitrage profit.

This is architecturally identical to the reported issue: a protocol action that moves user funds is gated by a single-DEX, single-block spot price rather than a TWAP or multi-source price, making it manipulable by anyone willing to trade against the pool in the same block window. The upgrade changelog confirms this quote-based flow (with only 5% slippage) was only recently introduced, replacing a previous 0-slippage call: [7](#0-6) 

### Impact Explanation
The victim is the ordinary user submitting a `GAS` or `GAS_AND_PAYLOAD` inbound (gas abstraction top-up) or the recipient of an outbound gas refund. Their PRC20 deposit is converted to native PC via an autoswap whose floor price is derived from a manipulable spot quote, so a sandwiching attacker can permanently siphon value out of the user's deposit/refund on every affected inbound/outbound — this is "corruption of ... gas fee accounting / refund accounting" and effectively unauthorized value extraction from user-controlled funds during a module-originated EVM execution, matching the in-scope impact categories (loss of user funds via manipulated accounting in the universal execution/gas-refund path).

### Likelihood Explanation
Any unprivileged user can trade against Push Chain's own Uniswap V3 PRC20/WPC pool at will; no privileged role, validator collusion, or external chain compromise is required. The attack only requires the attacker to time an ordinary swap around processing of a target inbound/outbound whose value is large enough to make the extracted slippage profitable — a routine MEV/sandwich pattern that is directly enabled because the price check is single-source and computed in the same call as the swap, with only a generic 5% tolerance rather than a fair-value/TWAP-anchored bound.

### Recommendation
Anchor `minPCOut` to a manipulation-resistant reference price rather than a live spot quote from the same pool being traded:
- Use a Uniswap V3 TWAP (`observe`/oracle cardinality-based) over a window long enough to resist single-block manipulation, or
- Combine the on-chain quote with the chain-meta gas-price oracle / an independent price source and reject swaps whose live quote deviates materially from the trusted reference, or
- Reduce the slippage tolerance and add a maximum-deviation check between the live quote and a stored/previous TWAP snapshot before allowing the autoswap to proceed, reverting instead (fail-safe) if the discrepancy is large.

### Proof of Concept
1. Attacker observes (or triggers) an in-flight `GAS_AND_PAYLOAD` or `GAS` inbound with a sizeable PRC20 amount destined for autoswap.
2. In the same block (or immediately preceding block, before validator votes finalize the inbound and trigger `ExecuteInboundGas`), attacker submits a large swap into the same PRC20/WPC Uniswap V3 pool used by `GetUniversalCoreQuoterAddress`/`GetSwapQuote`, depressing the pool's exchange rate.
3. When the module executes `GetSwapQuote` → `minPCOut = quote*95/100` → `CallPRC20DepositAutoSwap` (`x/uexecutor/keeper/execute_inbound_gas.go` lines 126–153, `x/uexecutor/keeper/evm.go` lines 500–592), the quote reflects the manipulated pool state, so the victim's deposit is swapped at a price far below fair value while still satisfying the 5%-derived floor.
4. Attacker reverses their initial trade, restoring the pool and pocketing the difference extracted from the victim's autoswap output.

Note: I could not fully verify the AMM pool's liquidity depth, whether it is thinly traded (making the attack cheaper), or whether any external guard (e.g., a maximum trade size cap or oracle deviation check) exists elsewhere in the `UniversalCore` Solidity contract itself, since that contract's source was not available in the indexed code — only its ABI was inspected. A Devin session with full repository/contract access would be needed to confirm whether `UniversalCore.depositPRC20WithAutoSwap` enforces any additional on-chain price-deviation protection beyond the caller-supplied `minPCOut`.

### Citations

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

**File:** x/uexecutor/keeper/outbound.go (L259-270)
```go
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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
