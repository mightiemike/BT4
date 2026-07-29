Found the analog. This is the actual bug-class match to the flashloan fee-manipulation issue: a swap price (analogous to AlgebraPool's dynamic fee) that is quoted at execution time and can be moved by an attacker's own transaction beforehand in the same block, harming a party who doesn't control the timing of their own "swap."

### Title
Auto-swap deposit quote can be sandwiched by an attacker manipulating the Uniswap V3 pool in the same block, forcing depositors to receive a worse PC price - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund` all fetch a swap quote via `GetSwapQuote` (Uniswap V3 `QuoterV2.quoteExactInputSingle`) immediately before calling `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`, and apply a fixed 5% slippage tolerance (`minPCOut = quote * 95 / 100`) computed from that single quote. [1](#0-0) [2](#0-1) [3](#0-2)  This mirrors the AlgebraPool bug class: the fee/price a victim (module-driven action) pays is set by whatever pool state exists at the moment the transaction executes, and an unprivileged actor can move that state right before it.

### Finding Description
The quote (`GetSwapQuote`) and the resulting `minPCOut` bound are derived entirely from the live on-chain Uniswap V3 pool price at execution time. [4](#0-3)  The module-originated auto-swap deposit is triggered deterministically by finalized validator votes (`VoteInbound`/`VoteOutbound` reaching quorum), which land in an ordinary block alongside normal user transactions. Any unprivileged user can submit an EVM transaction against the same Uniswap V3 pool (buying PC with the PRC20 gas token, or vice versa) positioned earlier in the block's transaction ordering, moving `sqrtPriceX96` away from its fair value before the module's `depositPRC20WithAutoSwap` executes. Because the 5% slippage window is computed from the manipulated quote (not from a price the depositor/refund-recipient can independently verify or reject), the attacker can push the effective price up to ~5% worse than fair value and pocket the difference via a follow-up trade — a classic sandwich, but enabled by the same root cause as the referenced report: a swap fee/price parameter set by pre-existing in-block state that the victim didn't choose and can't bound tighter than the module's hardcoded tolerance.

### Impact Explanation
Every GAS and GAS_AND_PAYLOAD inbound deposit, and every excess-gas refund on outbound completion, routes PRC20 → PC value through this quote-then-swap pattern. [5](#0-4)  A depositor's or refund recipient's UEA can systematically receive up to 5% less PC than fair value on every affected inbound/outbound, extracted by an unprivileged attacker monitoring the mempool/thin liquidity pools registered by `x/uregistry`. This is a real, repeatable value-extraction vector against ordinary users (not admins, not validators) reachable purely through public EVM transactions against the pool contracts Push Chain itself designates via `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`.

### Likelihood Explanation
No privileged access is required — the attacker only needs to observe the mempool (or predict validator vote finalization, which is deterministic once quorum is reached) and submit ordinary swap transactions against the pool before the module's swap executes in the same block. Thinly liquid or low-volume PRC20/PC pools amplify the effect. This requires only unprivileged user transactions and honest validator/module behavior, matching the "ordinary user deposit path" scope.

### Recommendation
Do not derive `minPCOut` purely from a same-block spot quote. Options: (1) use a TWAP-based reference price (or a price bound configured/sanity-checked against an independent oracle) rather than `QuoterV2.quoteExactInputSingle`'s instantaneous quote; (2) tighten and make the slippage tolerance configurable per token/liquidity depth rather than a flat 5%; (3) where feasible, let the deposit skip the auto-swap and simply mint PRC20 without swapping when quote deviates significantly from a trusted reference, falling back the way `applyGasRefund` already does on outright swap failure.

### Proof of Concept
1. Attacker monitors pending validator votes on an inbound/outbound (public, observable) or the mempool for the module's `depositPRC20WithAutoSwap`/`refundUnusedGas` calls.
2. Attacker submits a large swap on the same Uniswap V3 pool (`prc20 -> wpc` or reverse) immediately before, moving `sqrtPriceX96` unfavorably for the upcoming module swap.
3. The module calls `GetSwapQuote` at that manipulated price and computes `minPCOut = quote * 95 / 100`, then executes `depositPRC20WithAutoSwap` (or `refundUnusedGas` with swap) at the manipulated price, within the 5% tolerance. [6](#0-5) 
4. Attacker immediately submits a reverse swap to restore the pool price, capturing the spread; the depositor's UEA/refund recipient receives less PC than fair value, up to the 5% bound, on every affected inbound/outbound.

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

**File:** x/uexecutor/keeper/outbound.go (L214-234)
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
