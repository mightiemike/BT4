## Analog Found: Fixed 5% Slippage on Swap-Quote-Then-Swap Executed Atomically From Manipulable Spot Price

### Title
Hardcoded 5% slippage computed from an unprotected, non-TWAP spot quote lets an attacker sandwich `depositPRC20WithAutoSwap` / `refundUnusedGas` swaps to steal bridged/refunded value - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every code path that swaps a bridged PRC20 (or a leftover gas-fee PRC20) into native PC computes its slippage guard as a fixed 5% of a quote fetched from Uniswap V3 `QuoterV2.quoteExactInputSingle` immediately before executing the swap in the very same keeper call: `minPCOut = quote * 95 / 100`. This mirrors the audited Backd `TopUpAction` bug exactly — a hardcoded default slippage tolerance with no user- or protocol-level ability to tighten it, and the "reference price" itself is nothing but the manipulable current spot price of the pool, not a TWAP or externally-verified price. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `sqrtPriceLimitX96 = 0`, returning the raw current spot-based amount out for the pool — no TWAP, no oracle cross-check: [4](#0-3) 

Immediately after, in the *same* keeper call (no intervening cross-check against an independent price source), `minPCOut` is derived purely as 95% of that just-fetched quote: [5](#0-4) 

The same pattern repeats for `GAS_AND_PAYLOAD` inbound execution: [6](#0-5) 

and for the gas-refund path executed on outbound vote finalization: [7](#0-6) 

Because the Uniswap V3 pool used for PRC20↔WPC swaps is a normal permissionless AMM pool (deployed/seeded per the e2e setup scripts), any unprivileged party can submit ordinary swap transactions against it. The inbound/outbound finalization itself is driven by ordinary `MsgVoteInbound` / outbound-observation transactions from Universal Validators, whose submission (and therefore the block in which the deposit-swap or refund-swap actually executes) is visible in the mempool ahead of inclusion. An attacker who is not a validator, not privileged, and not part of TSS can:

1. Observe the pending vote transaction that will trigger `CallPRC20DepositAutoSwap` (or `refundUnusedGas`).
2. Front-run it with a large swap in the same PRC20/WPC pool to push the spot price down.
3. Because `GetSwapQuote` is only called *after* this manipulation, inside the same block, the "protection" quote it returns already reflects the manipulated price — the 5% band is computed on top of an already-depressed number, so it provides no real protection.
4. The deposit-autoswap or refund-autoswap executes at the manipulated price, converting the user's bridged principal (or the user's refunded excess gas) into far less native PC than the true market price would produce.
5. The attacker back-runs with the reverse swap to restore the pool price and pocket the difference.

This is the same structural flaw as the referenced Backd finding: a fixed, non-configurable slippage tolerance derived from a spot quote fetched right before the protected action, giving no defense against price manipulation that occurs before that quote is even taken.

### Impact Explanation
This directly causes permanent, protocol-mediated loss of user funds: the amount of native PC actually credited to the user's UEA (on inbound GAS / GAS_AND_PAYLOAD execution) or to the refund recipient (on outbound gas refund) can be forced down by up to ~5% (or more, since the "reference" price is itself attacker-set) per swap, with the difference captured by the sandwiching attacker. This falls squarely within the allowed impact of "permanent loss ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting, refund accounting."

### Likelihood Explanation
The attack requires only being able to submit ordinary swap transactions on the PRC20/WPC pool and observe pending validator vote transactions — no privileged role, validator key, or TSS access is needed. It scales with any pool that has moderate-to-low liquidity relative to the swapped amount, which is realistic for newly onboarded PRC20 tokens. Likelihood is Medium-High given how routine and repeatable these deposit/refund swap calls are (every GAS and GAS_AND_PAYLOAD inbound, and every outbound with excess gas fee).

### Recommendation
- Do not derive `minPCOut` solely from a same-transaction spot quote from the AMM being swapped against. Use a time-weighted average price (TWAP) from the pool (or an independent oracle) as the reference price for the slippage bound.
- Allow a configurable/tighter maximum slippage (e.g., a module/registry parameter, or ideally something closer to the true market spread) instead of a hardcoded 5%, and consider making it explicit per-token so illiquid pools can enforce tighter bounds or disable auto-swap.
- Consider requiring the quote to be fetched at a different block height/commitment than the swap execution, or use a circuit breaker that reverts (falls back to no-swap deposit, which the code already supports) if the spot price deviates materially from a longer-window reference.

### Proof of Concept
1. Attacker identifies a PRC20/WPC Uniswap V3 pool with modest liquidity used by `GetDefaultFeeTierForToken` for a bridged asset.
2. Attacker watches the Push Chain mempool/validator gossip for the 2/3-quorum `MsgVoteInbound` (or outbound-observation) transaction that will trigger `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` / `applyGasRefund` for a sizable bridged amount.
3. Attacker submits a large swap in the same pool just before that transaction lands, depressing the PRC20→WPC price.
4. The validator's vote transaction executes; `GetSwapQuote` returns the already-depressed price; `minPCOut = quote*95/100` is computed from this manipulated number and passed to `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`.
5. The deposit/refund swap executes at the manipulated price, crediting the user's UEA/refund recipient with less native PC than fair value.
6. Attacker submits the reverse swap immediately after to restore the pool price, capturing the difference extracted from the user's deposit/refund — a classic sandwich, with no user-configurable slippage or TWAP protection available to prevent it, mirroring the Backd `TopUpAction.sol` [M-11] finding.

### Citations

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
