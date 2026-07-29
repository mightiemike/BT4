This confirms the analog. The pattern is identical across three code paths — `x/uexecutor/keeper/execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `outbound.go` — all deriving `minPCOut` from a spot-price `QuoterV2.quoteExactInputSingle` call fetched immediately before the swap executes, exactly mirroring the `USDLStrategy._swapPLSforUSDL()` pattern in the external report. The upgrade handler at `app/upgrades/chain-meta/upgrade.go:62-67` even documents this as an intentional design change (replacing 0-slippage with 5% on-chain quote), confirming there's no TWAP/oracle-based protection anywhere in the codebase.

### Title
On-chain spot-price Uniswap V3 quotes for `minPCOut` slippage protection enable sandwich attacks on every gas-swap, payload-swap, and gas-refund-swap — ([File: x/uexecutor/keeper/evm.go, execute_inbound_gas.go, execute_inbound_gas_and_payload.go, outbound.go])

### Summary
Push Chain's `x/uexecutor` module computes `minPCOut` slippage protection for every PRC20→WPC auto-swap (inbound gas top-up, gas+payload deposit, and outbound excess-gas refund swap) by calling Uniswap V3's `QuoterV2.quoteExactInputSingle` for a live spot quote and applying a flat 5% tolerance (`quote * 95 / 100`), then immediately executing the swap against that bound. This is the same anti-pattern flagged in the external report: an on-chain, same-transaction spot-price quote used as slippage protection is not resistant to price manipulation, and every swap executed this way can be sandwiched, within the 5% band, at the expense of users receiving less PC than the fair market rate.

### Finding Description
`GetSwapQuote` in [1](#0-0)  calls `QuoterV2.quoteExactInputSingle` to fetch the current spot price of the PRC20/WPC pool. This value is used directly, with only a fixed 5% haircut, as the `minPCOut` slippage floor passed into `CallPRC20DepositAutoSwap` (which performs the real swap via `depositPRC20WithAutoSwap`) in:

- `ExecuteInboundGas` (`GAS` inbound route): [2](#0-1) 
- `gasAndPayloadDepositAutoSwap` (`GAS_AND_PAYLOAD` inbound route): [3](#0-2) 
- `applyGasRefund` (excess-gas refund on outbound finalization): [4](#0-3) , using `getSwapQuoteForRefund` at [5](#0-4) 

Because the quote comes from the live AMM pool state (not a TWAP or external price oracle), any attacker who can shift the PRC20/WPC pool reserves shortly before the module's swap executes can push the spot quote to a manipulated level and still remain inside the 5% slippage band, extracting value that would otherwise accrue to the user. The `app/upgrades/chain-meta/upgrade.go` migration notes at [6](#0-5)  confirm this spot-quote + 5% design was deliberately introduced to replace a prior 0-slippage call, and no TWAP or oracle-based price source exists anywhere in the swap path.

Unlike a typical user-submitted DEX trade (where the user controls timing and can choose their own slippage tolerance), these swaps are module-originated and execute deterministically once validator ballot finalization completes (inbound voting quorum, or outbound observation quorum for the refund case). An attacker who monitors pending inbound/outbound votes can predict exactly which block will contain the module's `DerivedEVMCall` swap and submit surrounding transactions (buy PRC20/WPC before, sell after) to capture the spread, since it is not the user picking `minPCOut` — the protocol picks it from a manipulable spot source.

### Impact Explanation
Every user who bridges funds into Push Chain via the `GAS` or `GAS_AND_PAYLOAD` inbound routes, or who receives an excess-gas refund with a swap leg, systematically receives up to ~5% less native PC than fair value, with the difference captured by an unprivileged sandwiching attacker. This is a repeatable value-extraction vector against ordinary user deposit/refund flows (not a privileged actor), degrading PRC20/native asset accounting outcomes for every affected transaction. It mirrors the original finding's rated severity (medium/BVSS 5.0): individually small per-transaction leakage, but systemic and reachable by any unprivileged actor with capital to manipulate the pool.

### Likelihood Explanation
High for pools with thin liquidity relative to swap size, which is common for a young chain's initial PRC20/WPC pools. The attacker only needs to observe pending `MsgVoteInbound`/`MsgVoteOutbound` finalization or mempool activity to predict the block in which the module's deterministic swap will land, then sandwich within the 5% tolerance — no privileged access, validator collusion, or protocol bug bypass is required.

### Recommendation
Replace the immediate spot `quoteExactInputSingle` call with a TWAP-based price source (e.g., Uniswap V3 pool `observe()` over a meaningful window) or an external price oracle for computing `minPCOut`, and/or tighten and make configurable the slippage tolerance rather than a fixed 5%. Consider also enforcing a maximum single-swap size relative to pool liquidity, or routing gas-swap amounts through pools deep enough to make sandwich profit economically negligible.

### Proof of Concept
1. Attacker observes a pending `Inbound` with `TxType = GAS` or `GAS_AND_PAYLOAD` reaching ballot quorum (or an `OutboundObservation` reaching quorum with excess gas to refund).
2. Attacker predicts the block in which the module will call `GetSwapQuote` → `CallPRC20DepositAutoSwap` (or `CallUniversalCoreRefundUnusedGas` with `withSwap=true`) for the affected PRC20/WPC pool.
3. Attacker front-runs by buying WPC (or selling PRC20 into the pool) to depress the PRC20→WPC spot price just inside the 5% deviation window.
4. The module's `GetSwapQuote` call at [7](#0-6)  returns the manipulated spot quote; `minPCOut = quote * 95/100` is computed from this already-depressed value, so the subsequent swap still clears the check while delivering less PC to the user/recipient than fair value.
5. Attacker back-runs by reversing their trade, capturing the spread that was diverted from the user's expected PC output.

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

**File:** x/uexecutor/keeper/outbound.go (L213-230)
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
