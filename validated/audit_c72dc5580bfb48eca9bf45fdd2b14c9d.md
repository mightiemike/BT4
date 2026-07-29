This confirms the analog. `ExecuteInboundGas` (and `gasAndPayloadDepositAutoSwap`, `applyGasRefund`) fetch a single spot quote via `GetSwapQuote`/`QuoterV2.quoteExactInputSingle` [1](#0-0)  and derive `minPCOut` as a fixed 95% of that same quote, with no staleness, deviation-band, or manipulation-resistance check (no TWAP, no min/max sanity bound) — confirmed by the absence of any such guard in the codebase.

### Title
Auto-swap `minPCOut` derived from a single manipulable spot `QuoterV2` quote enables value extraction from gas-abstraction and gas-refund deposits - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report (D3Oracle M-5) is about a price consumer that only checks `price > 0` instead of validating the value against a min/max sanity band, letting a distorted price flow into fund-moving logic. The Push Chain analog is `Keeper.GetSwapQuote`, which reads `QuoterV2.quoteExactInputSingle` — a single, on-chain spot-price read on Push Chain's own Uniswap V3 fork — with no bound, staleness, or TWAP check at all (not even a `>0` check), and feeds that raw number directly into the slippage-protection parameter (`minPCOut`) for a protocol-controlled swap of user funds.

### Finding Description
`GetSwapQuote` unpacks `amountOut` straight from `quoteExactInputSingle` and returns it verbatim [2](#0-1) . Three call sites use this value as the *only* input to the slippage floor:

- `ExecuteInboundGas` (GAS inbound route): `minPCOut = quote * 95 / 100`, then `CallPRC20DepositAutoSwap` executes the swap [3](#0-2) .
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD route): identical pattern [4](#0-3) .
- `applyGasRefund` (outbound gas refund route): same pattern for `refundUnusedGas`'s `withSwap=true` path [5](#0-4) .

Because `minPCOut` is computed from the *same* pool state that the swap itself will execute against, and both operations happen within Push Chain's own EVM (no cross-chain latency), an attacker who moves the pool's spot price down immediately before the module's auto-swap executes (e.g., large sell of the PRC20/gas-token leg into WPC in the same block) causes `quote` to already reflect the depressed price. `minPCOut` is then only a 5% margin *below an already-manipulated number*, so it provides no real protection — the module-originated swap executes at the manipulated rate, and the attacker profits by reversing their trade afterward (classic sandwich), extracting value from the user's bridged deposit or gas refund. This is the same root-cause class as the Chainlink report: a price value is consumed for a financial computation without validating it sits within a reasonable/expected range or is resistant to manipulation — here there isn't even a lower/upper sanity bound, deviation check against a longer-window reference price, or TWAP fallback.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" and "unauthorized ... state transitions in universal execution flows" in the allowed-impact gate, because an unprivileged attacker can cause the protocol's own module-signed EVM call (`depositPRC20WithAutoSwap` / `refundUnusedGas`, both `DerivedEVMCall`s from the `ue` module account, i.e., protocol-controlled funds) to execute at an attacker-favorable price, draining value that should have gone to the depositing user's UEA or the gas-refund recipient. This directly and reachably drains/mis-routes user-destined value through the default GAS / GAS_AND_PAYLOAD inbound path and the outbound gas-refund path — no validator, admin, or TSS compromise is required.

### Likelihood Explanation
Reachable via ordinary user actions alone: any GAS or GAS_AND_PAYLOAD inbound (fee-abstraction bridging), or any outbound whose gas refund takes the swap branch, triggers this code path once validators finalize the corresponding vote. The attacker only needs to be able to trade against the same Uniswap V3 pool on Push Chain's EVM in the same block/transaction window as the module's auto-swap call — a standard sandwich primitive, requiring no validator or TSS collusion, matching the "ordinary unprivileged user actions alone" bar in the allowed-impact gate.

### Recommendation
Do not derive the slippage floor solely from the same spot quote that will be executed against. Options: (1) source a TWAP (time-weighted average) from the pool instead of `quoteExactInputSingle`'s instantaneous quote, and/or (2) compare the spot quote against a longer-window reference/oracle price and reject/clamp when deviation exceeds a configured band (analogous to Chainlink's recommended min/max sanity check), and/or (3) enforce a maximum single-block price-impact bound on the pool used for these system swaps, and/or (4) route these swaps through a pool with restricted/whitelisted liquidity providers or use a protocol-owned price oracle rather than the spot AMM quote alone.

### Proof of Concept
1. Attacker identifies the Uniswap V3 pool (fee tier from `GetDefaultFeeTierForToken`) used for the PRC20 gas-token ↔ WPC pair on Push Chain.
2. Attacker submits a large swap into that pool to push the spot price of the gas-token down relative to WPC.
3. In the same block, a user's GAS inbound (or GAS_AND_PAYLOAD inbound, or an outbound gas refund) is processed by validators, triggering `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`/`applyGasRefund`.
4. `GetSwapQuote` returns the depressed `amountOut`; `minPCOut = quote * 0.95` is computed from this already-bad number [6](#0-5) .
5. `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap against the manipulated pool, satisfying the (also manipulated) `minPCOut` floor, so it does not revert.
6. Attacker reverses their initial trade, capturing the difference between the fair price and the manipulated execution price — value that should have accrued to the depositing user's UEA (or the gas-refund recipient).

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-378)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L214-230)
```go
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
