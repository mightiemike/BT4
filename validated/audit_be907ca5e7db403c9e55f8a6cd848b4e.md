### Title
Same-block spot-price quote used for auto-swap `minPCOut` enables price-manipulation-driven value extraction on GAS / GAS_AND_PAYLOAD inbound execution - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The Hifi report describes a thinly-liquidated pool whose *share price* can be manipulated by an attacker (small initial deposit + direct large token transfer) so severely that other participants are forced to transact at an attacker-favorable ratio. The corresponding analog in Push Chain's scoped node code is the reliance on a single, same-block Uniswap V3 `QuoterV2.quoteExactInputSingle` **spot quote** (not a TWAP or any manipulation-resistant price source) to compute `minPCOut` for the protocol-driven `depositPRC20WithAutoSwap` call that executes automatically whenever a `GAS` or `GAS_AND_PAYLOAD` inbound is finalized. Because the underlying WPC/PRC20 pools can be thinly liquidated (same precondition as the Hifi report), an attacker can move the pool's spot price immediately before validators execute the inbound, causing the on-chain-computed `minPCOut` protection to reflect the manipulated price rather than a fair one.

### Finding Description
`GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) performs a single read-only call to `QuoterV2.quoteExactInputSingle` using the *current* pool state: [1](#0-0) 

This spot quote is used directly to compute the slippage floor with a fixed 5% tolerance and no other manipulation defenses (no TWAP, no oracle cross-check, no minimum liquidity check on the pool): [2](#0-1) 

The same pattern is repeated for `GAS_AND_PAYLOAD` inbound execution in `gasAndPayloadDepositAutoSwap`: [3](#0-2) 

and for outbound gas refunds via `getSwapQuoteForRefund`: [4](#0-3) 

There is no TWAP usage anywhere in the codebase (confirmed by grep), meaning every one of these protocol-driven swaps relies purely on the instantaneous, attacker-manipulable pool price. The change-log confirms this was a deliberate but incomplete mitigation: it replaced a "0-slippage" call with a 5%-tolerance quote-based call, explicitly acknowledging the manipulation surface without closing it: [5](#0-4) 

An unprivileged external actor can, in a preceding transaction on the same WPC/PRC20 Uniswap V3 pool (which can be thinly liquidated, exactly as in the Hifi report's precondition), push the spot price to an attacker-favorable point. Because Push Chain's `depositPRC20WithAutoSwap` call is triggered automatically and atomically by the honest validators the moment quorum on the inbound vote is reached, the attacker does not need to control block production or be a privileged actor — they only need to observe pending votes/mempool and land a manipulation trade in a block shortly before quorum-triggered execution. The 5% slippage window is computed off the *already-manipulated* price, so it fails to protect the depositing user; the attacker can then unwind their manipulation trade (arbitrage/backrun) capturing value that should have gone to the swap's `to` recipient (the user's UEA) or from the protocol's PC/PRC20 reserves.

### Impact Explanation
This falls within the "corruption of PRC20 or native asset accounting ... revert destination ... canonical UniversalTx state" and "unauthorized module-originated EVM execution" impact categories: the protocol-originated `DerivedEVMCall` (`CallPRC20DepositAutoSwap`) executes with a `minPCOut` value that does not reflect a fair, unmanipulated price, resulting in less PC being credited to the user's UEA than intended, with the difference extractable by the attacker via the pool. This is fund-level value leakage triggered purely by unprivileged user/attacker action against honest validators and honest node code — no malicious validator, relayer, or TSS participant needed.

### Likelihood Explanation
Feasibility depends on the liquidity depth of the specific WPC/PRC20 pool being swapped through and on mempool/timing visibility of pending inbound votes reaching quorum. For newly listed or low-liquidity PRC20 assets (structurally identical to Hifi's "smaller liquidity provider" scenario), the capital required to move price meaningfully within the 5% tolerance band is low, making this practically exploitable; for deep, well-arbitraged pools the cost rises. Likelihood is therefore asset/pool-dependent but structurally present for any newly onboarded or thin PRC20/WPC pair, which is a realistic and recurring condition given Push Chain onboards new tokens over time.

### Recommendation
Do not rely solely on a same-block `QuoterV2` spot quote for `minPCOut`. Use a manipulation-resistant reference price (e.g., a time-weighted average from the pool's oracle observations, or a governance/registry-configured reference price with a bounded deviation check against the spot quote) before computing `minPCOut`, and/or widen protections by capping the auto-swap size relative to pool liquidity, similar in spirit to Uniswap V2's permanently-locked minimum liquidity mitigation for the analogous first-LP attack — the underlying principle in both cases is "don't let a cheap, attacker-controlled instantaneous state manipulation set the terms of a protocol-critical calculation."

### Proof of Concept
1. Attacker identifies a thinly-liquidated WPC/PRC20-X Uniswap V3 pool used for `GAS`/`GAS_AND_PAYLOAD` inbound auto-swaps (`GetUniversalCoreWPCAddress`/`GetDefaultFeeTierForToken` in `x/uexecutor/keeper/evm.go`).
2. Attacker submits (or observes) a pending `MsgVoteInbound` sequence for a `GAS_AND_PAYLOAD`/`GAS` inbound about to reach 2/3 quorum, targeting PRC20-X.
3. Immediately before/around quorum being reached, attacker submits a large swap against the WPC/PRC20-X pool to shift the spot price adversely to the depositor.
4. Validators reach quorum; `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` call `GetSwapQuote` → 95% of the now-manipulated spot quote → `CallPRC20DepositAutoSwap` executes at the manipulated ratio (`x/uexecutor/keeper/execute_inbound_gas.go:104-153`).
5. Attacker reverses their manipulation trade, extracting the spread; the depositing user's UEA receives materially less PC than a fair-price swap would have produced.

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
