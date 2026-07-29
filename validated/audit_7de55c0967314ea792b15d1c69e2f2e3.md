### Title
Fixed 5% slippage tolerance on internal PRC20→PC auto-swaps enables sandwich extraction from user deposits and gas refunds - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report's core concern — a swap function accepting attacker-influenced execution price with no meaningful slippage protection — has a direct analog in Push Chain's `x/uexecutor` module. Every internal auto-swap path (gas-abstraction deposit, gas-and-payload deposit, and outbound gas refund) fetches a Uniswap V3 quote and then hardcodes `minPCOut = quote * 95 / 100`, i.e., an unconditional 5% slippage tolerance that is neither user-configurable nor bounded by any sanity/maximum check. This is exactly the anti-pattern the report itself warns against ("hardcoded value here for slippage could result in freezing of funds ... or attacker manipulation").

### Finding Description
Three call sites compute a swap's minimum acceptable output by taking a live on-chain quote and unconditionally discounting it by 5%: [1](#0-0) [2](#0-1) [3](#0-2) 

In each case, the flow is:
1. `GetSwapQuote` performs a static (`commit=false`) call to `QuoterV2.quoteExactInputSingle` to get the current spot-equivalent output for the trade. [4](#0-3) 
2. `minPCOut` is derived purely as `quote * 95 / 100`, with no per-trade-size cap, no maximum absolute slippage limit, and no dynamic adjustment based on trade size vs. pool depth.
3. `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` then execute the real swap via `DerivedEVMCall`, passing this `minPCOut` and a `deadline = 0` (meaning "contract uses its default", not attacker/user supplied). [5](#0-4) 

Because the quote-fetch and the swap-commit are two separate EVM calls (not atomic at the Cosmos block level, and the swap only executes after a ballot is finalized by ≥2/3 validator votes across multiple `MsgVoteInbound`/`MsgVoteOutbound` transactions), there is an observable, non-trivial window between "price used to compute the floor" and "price at which the swap actually executes." An unprivileged attacker who holds LP/trading access to the Uniswap V3 pool backing the `PRC20↔WPC` pair (a pool anyone can trade against, since Push Chain's PRC20/WPC pools are ordinary DeFi pools, not permissioned) can:
- Observe pending inbound/outbound votes (mempool-visible `MsgVoteInbound`/`MsgVoteOutbound` transactions or predictable finalization timing once quorum is close),
- Push the pool price down right before the validator vote that finalizes the ballot and triggers the swap (a "front-run" leg),
- Let the module's swap execute at up to 5% worse pricing than fair value (protected floor still allows up to a full 5% loss),
- Restore the price afterward (the "back-run" leg), pocketing up to 5% of the swapped notional value extracted from the depositing user's or protocol's PRC20/PC accounting.

This mirrors the report's finding precisely: `swapExactTokensForETHSupportingFeeOnTransferTokens(tokenAmount, 0, ...)` in the report had *no* slippage bound at all; here the bound exists but is a hardcoded, non-adaptive 5%, which the report's own recommendation explicitly calls out as insufficient ("setting a hardcoded value here for slippage could result in freezing of users funds in times of high volatility" — and, by the same logic, allows up to that full hardcoded margin to be extracted by a sandwiching attacker regardless of real market conditions or trade size).

### Impact Explanation
This corrupts PRC20/native-asset accounting for the gas-abstraction and gas-refund swap legs: users depositing a gas token (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`) or receiving unused-gas refunds (`applyGasRefund`) can systematically receive up to 5% less PC/native value than fair market price would provide, with the difference captured by an attacker manipulating the underlying Uniswap V3 pool around the block boundary where ballot finalization triggers the swap. For large gas-token deposits or refunds this is a direct, repeatable value-extraction vector against ordinary users, falling under "corruption of PRC20 or native asset accounting … refund accounting" and "unauthorized module-originated EVM execution" losing user value, per the allowed-impact scope.

### Likelihood Explanation
Medium. The attacker does not need any privileged role — only the ability to trade against the pool backing the relevant PRC20/WPC pair (which appears to be a normal, unprivileged-tradable Uniswap V3 pool seeded by the protocol/e2e setup). The trigger condition (ballot finalization timing) is partially observable since finalization happens synchronously inside `MsgVoteInbound`/`MsgVoteOutbound` transaction processing once the quorum vote lands, and votes from Universal Validators may be predictable in cadence. The bound is fixed at exactly 5% regardless of trade size or pool liquidity, so smaller/thinner pools make the attack cheaper and more consistently profitable up to the full band.

### Recommendation
Do not hardcode a flat percentage-based slippage bound decoupled from pool liquidity and trade size. Instead:
- Compute `minPCOut` using a much tighter, liquidity/depth-aware tolerance (e.g., derived from the pool's TWAP over a window resistant to single-block manipulation, not the instantaneous `quoteExactInputSingle` spot value), and/or
- Cap the maximum allowed slippage to a much smaller bound (e.g., 0.5–1%) with a circuit breaker that reverts to the no-swap direct-deposit path (which the code already supports via `withSwap=false`) if slippage would exceed that bound, and
- Set a real `deadline` (bounded number of blocks from now) instead of `0`, to prevent stale-quote execution.

### Proof of Concept
Conceptual walk-through (not runnable without a live pool/fork):
1. Attacker monitors mempool for `MsgVoteInbound` transactions on a gas-abstraction inbound (`TxType_GAS`/`GAS_AND_PAYLOAD`) that is one vote away from quorum (`votesNeeded`).
2. Immediately before the finalizing vote lands, attacker submits a large swap against the same PRC20↔WPC Uniswap V3 pool that `GetSwapQuote`/`CallPRC20DepositAutoSwap` will use, pushing the effective price against the pending deposit direction.
3. The finalizing `MsgVoteInbound` executes `ExecuteInboundGas` → `GetSwapQuote` (now reflecting the manipulated price) → `minPCOut = quote*95/100` → `CallPRC20DepositAutoSwap`, executing at the manipulated price, still satisfying the (also manipulated, still generous) 5%-off floor. [1](#0-0) 
4. Attacker immediately reverses their swap in a following transaction, restoring the pool price and capturing the spread — the difference between the true pre-attack fair value and the manipulated execution price, bounded above by the fixed 5% tolerance times deposit notional.

Note: I was not able to inspect the on-chain Solidity `UniversalCore`/`depositPRC20WithAutoSwap` contract source (out of index) to confirm whether it enforces any additional deadline or TWAP protection beyond what the Go keeper passes in; the `deadline=0` and 5%-fixed-band logic on the Go side is confirmed directly from the repository.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-379)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L214-237)
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
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
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

**File:** x/uexecutor/keeper/evm.go (L574-593)
```go
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
}
```
