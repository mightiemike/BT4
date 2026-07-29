Confirmed: `GetSwapQuote` uses `QuoterV2.quoteExactInputSingle` — a live, single-block Uniswap V3 spot quote (`sqrtPriceLimitX96 = 0`, no TWAP) — and this quote is fetched and consumed atomically, in the very same Push Chain transaction, as the `CallPRC20DepositAutoSwap` / `depositPRC20WithAutoSwap` execution that performs the real swap. The slippage bound (`minPCOut = quote * 95 / 100`) is derived from that same manipulable spot price, not an independent, delayed, or TWAP-based reference. This is architecturally identical to the reLP finding's flaw: a swap whose slippage protection is computed from a spot price fetched immediately adjacent to the swap itself, executed as an automatic side effect of a fully unprivileged, attacker-sized user action (an inbound deposit amount is entirely attacker-controlled, exactly like the `bond()` amount in the original report).## Analysis

The reLP finding's core defect is: **a swap with slippage protection computed from a live, single-block spot price is executed as an automatic side effect of an unprivileged, attacker-sized user action** — letting the user pick the trade size and (via price manipulation immediately before it lands) walk away with value at the expense of the pool/protocol.

Push Chain's `x/uexecutor` inbound gas-abstraction/auto-swap path reproduces the exact same pattern.

### Title
Attacker-sized gas-abstraction auto-swap uses a same-transaction Uniswap V3 spot quote with only 5% slippage, enabling sandwich extraction on inbound execution - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
`ExecuteInboundGas` and `gasAndPayloadDepositAutoSwap` (called from `ExecuteInboundGasAndPayload`) fetch a Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote and immediately consume it, in the same keeper call, to compute `minPCOut` for a real swap issued via `CallPRC20DepositAutoSwap` (`depositPRC20WithAutoSwap`). Both the swap amount and the triggering event (an inbound deposit) are fully attacker-controlled, and the slippage bound is only 5%, computed from an unprotected single-block price with `SqrtPriceLimitX96 = 0` and no TWAP.

### Finding Description
The relevant path:
- `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `sqrtPriceLimitX96 = 0` — a pure spot-price quote of the underlying Uniswap V3 pool, taken at the exact moment of execution: [1](#0-0) 
- Immediately after, `minPCOut` is derived from that same spot quote with a flat 5% tolerance, and the real swap is issued via `CallPRC20DepositAutoSwap`/`depositPRC20WithAutoSwap` in the very same execution: [2](#0-1) 
- The identical pattern is used for `GAS_AND_PAYLOAD` inbounds: [3](#0-2) 
- And again for outbound gas-fee-excess refunds: [4](#0-3) 

This is triggered by fully unprivileged activity: any user can deposit an arbitrary `amount` of a whitelisted asset on an external chain gateway, producing a `GAS` or `GAS_AND_PAYLOAD` inbound whose `Amount` field flows unchanged into `GetSwapQuote`/`CallPRC20DepositAutoSwap` — the attacker controls trade size exactly the way the original report's attacker controlled `bond()`'s `_amount`, which sized the `removeLiquidity`/`swapExactTokensForTokens` trade in `reLPContract.reLP()`.

The execution itself is not directly attacker-called (it runs when Universal Validator votes on `MsgVoteInbound` cross the 2/3 threshold), but this vote-accumulation state is fully observable on-chain via `PendingInbounds` before finalization, letting an attacker predict which upcoming Cosmos block will execute the swap and place a manipulating trade against the pool referenced by the same `quoterAddr`/`wpcAddr`/`prc20` pair immediately before it, exactly as described for the reLP "sandwich without needing the exact mempool position" scenario — the attacker doesn't need to call the vulnerable function itself, only to time an ordinary swap against the predictable, attacker-sized execution.

### Impact Explanation
5% slippage against a single-block spot quote (no TWAP, no external oracle cross-check) leaves ample room for value extraction: an attacker who moves the pool price before the module's `depositPRC20WithAutoSwap` executes causes the module to swap the user's own deposited PRC20 for PC at a manipulated rate that still clears the loose 95%-of-quote floor, letting the attacker (who also controls the recipient UEA, since they are the depositor) realize a profit funded by the Uniswap V3 pool's other LPs, while the `minPCOut` guard that was meant to protect this exact conversion is computed off the same manipulated price and therefore offers no real protection. This directly corrupts PRC20/native asset accounting and gas-fee-refund accounting reachable purely from ordinary user deposits, matching the in-scope "corruption of PRC20 or native asset accounting ... revert instructions" and "unauthorized module-originated EVM execution" impact classes.

### Likelihood Explanation
Trade size is arbitrarily controllable per-deposit and repeatable across many inbounds/outbounds (no cap tying `amount` to pool depth), and the finalizing block is predictable from public `PendingInbounds`/vote state, so the attack does not require compromising or colluding with any validator, UV, or admin — it is reachable by an ordinary unprivileged user, consistent with the required threat model.

### Recommendation
Replace the single-block spot quote with a TWAP-based reference price (or an external price oracle) for computing `minPCOut`, tighten the slippage tolerance, and/or cap the per-inbound/per-outbound swap notional relative to the pool's available liquidity so that no single attacker-sized deposit can move the price enough to make the 5% band exploitable.

### Proof of Concept
1. Attacker deposits an arbitrary large `amount` of a whitelisted gas token on a source-chain gateway, producing a `GAS`/`GAS_AND_PAYLOAD` inbound addressed to their own UEA.
2. Attacker watches `PendingInbounds` on Push Chain; once 2/3 UV votes are close to finalizing, they submit an ordinary EVM swap on the Push-Chain-hosted Uniswap V3 pool (`prc20`/`WPC`) used by `quoterAddr`, pushing the spot price in the direction that benefits their upcoming auto-swap.
3. The finalizing `MsgVoteInbound` triggers `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`, which calls `GetSwapQuote` against the now-manipulated pool and computes `minPCOut = quote*95/100` off that manipulated price.
4. `CallPRC20DepositAutoSwap` executes the deposit-and-swap at the manipulated rate, still clearing the loose `minPCOut` floor, crediting the attacker's UEA with more PC than a fair-price swap would have produced.
5. Attacker reverses their manipulating trade (or lets arbitrageurs restore price), realizing profit funded by the pool's other liquidity providers/protocol funds. [1](#0-0) [2](#0-1) [3](#0-2)

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
