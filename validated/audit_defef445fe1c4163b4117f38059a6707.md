### Title
Hard-coded 5% slippage on protocol-driven PRC20→WPC auto-swaps enables sandwich extraction from user deposits - ([File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/outbound.go])

### Summary
The `GAS` and `GAS_AND_PAYLOAD` inbound execution paths, and the outbound gas-refund path, all auto-swap PRC20 tokens for WPC/PC through a Uniswap V3 `QuoterV2` using a fixed, unconditional 5% slippage tolerance (`minPCOut = quote * 95 / 100`). This mirrors the external report's core flaw — a static, oversized price-deviation cap applied uniformly regardless of trade size, market depth, or attacker capability — except here the swap is *protocol-initiated on behalf of the user*, not a user-chosen order, so the user has no way to opt for a tighter bound at all.

### Finding Description
`ExecuteInboundGas` fetches a live quote via `k.GetSwapQuote` and immediately computes `minPCOut := quote * 95 / 100`, then calls `k.CallPRC20DepositAutoSwap`, which issues a `DerivedEVMCall` to `depositPRC20WithAutoSwap` on the `UniversalCore`/Handler contract: [1](#0-0) 

The same fixed 5% pattern is repeated for `GAS_AND_PAYLOAD` deposits: [2](#0-1) 

and for excess-gas refunds on successful outbounds: [3](#0-2) 

The quote itself is a spot read of the AMM pool state at call time, with no TWAP protection: [4](#0-3) 

This entire flow is triggered deterministically the moment an inbound ballot reaches the `PASSED` terminal state during `VoteInbound`/ballot finalization — a state transition an unprivileged external attacker can observe by watching pending validator votes and mempool activity, and can even help trigger by submitting the final observation via an honest-relayer flow or by simply waiting for it. Because the quote-then-swap sequence executes within the same block/transaction as the module's `DerivedEVMCall`, an attacker can:
1. Front-run with a large WPC→PRC20 (or PRC20→WPC) swap on the same underlying Uniswap V3 pool to move the spot price against the pending deposit just before the module's `GetSwapQuote`/`CallPRC20DepositAutoSwap` executes in that block.
2. Let the module's auto-swap execute at the manipulated price — the flat 5% band accepts this without any check on absolute pool depth, historical price, or trade-size-relative impact.
3. Back-run to reverse the price move, capturing the ~5% (or up to that ceiling) value difference that would otherwise have gone to the user's deposit conversion (or to the protocol/refund recipient in the outbound-refund case).

Unlike the Deriverse report — where the ±12.5% band affects a user's own voluntary market order — here the swap is imposed by the protocol on the user's funds with no consent path and no way to tighten it, making the fixed percentage strictly a protocol-accounting/value-preservation guarantee, not a user risk choice.

### Impact Explanation
This directly falls under "corruption of PRC20 or native asset accounting" and "stealing/draining ... of user or protocol-controlled funds" in the allowed impact gate: an unprivileged attacker with pool-manipulation capability (financeable via flash loans, no privileged role required) can systematically skim up to ~5% of value from every GAS/GAS_AND_PAYLOAD inbound deposit and every outbound gas refund that meets the swap-and-refund path, at the expense of depositing users and/or the protocol's own accounting of `minPCOut`.

### Likelihood Explanation
Likelihood depends on the liquidity depth of the specific PRC20/WPC Uniswap V3 pool relative to attacker capital, and on the attacker's ability to land transactions in the same block as the module-triggered swap (proposer/mempool ordering). For thinly-liquid PRC20 pools, this is realistically exploitable with flash-loaned capital; for deep pools it is harder. The trigger condition (ballot reaching PASSED) is fully attacker-observable, and no validator/relayer collusion is required — only an unprivileged trader interacting with the public AMM pool.

### Recommendation
Replace the static 5% tolerance with a size- and liquidity-aware slippage model (e.g., derived from a TWAP oracle rather than the instantaneous `quoteExactInputSingle` spot quote, or capped as an absolute value proportional to expected pool depth). Consider batching/delaying swap execution or using a commit-reveal / private-mempool relay for the module's `DerivedEVMCall` to reduce sandwich visibility, and add a configurable, protocol-governed maximum slippage parameter instead of hard-coding `95`/`100` in three separate call sites.

### Proof of Concept
Conceptual (not runnable without a live pool/testnet):
1. Attacker observes a pending inbound whose ballot is one vote away from `PASSED` (public validator vote state).
2. Attacker submits a large swap on the PRC20/WPC Uniswap V3 pool in the same block as the vote that finalizes the ballot, timed to land immediately before the module's `DerivedEVMCall` for `depositPRC20WithAutoSwap` (or `refundUnusedGas`).
3. `GetSwapQuote` reads the manipulated spot price; `minPCOut = quote*95/100` is computed from that already-degraded price.
4. The module's swap executes within the wide band, converting the user's PRC20 into WPC/PC at the manipulated rate.
5. Attacker back-runs to restore the pool and realizes the captured spread, funded from the user's deposit conversion shortfall.

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
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
