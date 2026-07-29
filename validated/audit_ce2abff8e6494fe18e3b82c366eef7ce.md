Confirmed: `GetSwapQuote` uses `quoteExactInputSingle` with `SqrtPriceLimitX96: big.NewInt(0)` (no price-limit protection), computed synchronously at execution time with a fixed 5% slippage buffer, and the module-side code has no TWAP or independent price sanity check anywhere in `x/uexecutor/keeper/evm.go`. This confirms the analog is reachable and unguarded.

### Title
Gameable spot-price swap quote lets an unprivileged user extract value from GAS-deposit auto-swap and gas-refund flows via same-block price manipulation - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report shows a reward calculation (`amountMinted = pool.getRate(tbyId).mulWad(amount)`) that trusts a rate input which an attacker can pick to be favorable, producing a payout disproportionate to actual value contributed. The same class of bug — a protocol-controlled fund-movement decision driven by an attacker-influenceable, unprotected instantaneous price read — exists in Push Chain's `GetSwapQuote` helper, which is used to compute `minPCOut` for every PRC20→PC auto-swap performed by the `uexecutor` module on behalf of ordinary users (`GAS` / `GAS_AND_PAYLOAD` inbound deposits and outbound gas-fee refunds).

### Finding Description
`GetSwapQuote` in [1](#0-0)  calls Uniswap V3 QuoterV2's `quoteExactInputSingle` with `SqrtPriceLimitX96: big.NewInt(0)`, i.e. an unbounded, purely spot-price read with no TWAP or moving-average protection. The result is used to derive `minPCOut = quote * 95 / 100` — a fixed 5% slippage tolerance computed from that single spot read — in three places that all move protocol/user funds without any additional sanity check against an independent price source:

- `ExecuteInboundGas` (GAS inbound deposit-with-autoswap) [2](#0-1) 
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound) [3](#0-2) 
- `applyGasRefund` (outbound gas-fee refund-with-swap) [4](#0-3) 

Each of these paths is reachable via ordinary, unprivileged user actions: submitting a `GAS`/`GAS_AND_PAYLOAD` inbound deposit from any external chain, or having an outbound with unused gas that gets voted OBSERVED by honest UVs. Because the quote and the swap both execute synchronously as part of the same module-originated `DerivedEVMCall` in the same Push Chain block/transaction that finalizes the triggering ballot, and there is no TWAP/oracle cross-check, an attacker who can influence the pool's spot price at that specific block (e.g., by holding a large position in the underlying Uniswap V3 pool and executing a manipulative swap immediately before their own inbound/outbound is finalized) can move the reported `quote` down. The `minPCOut` floor computed from that manipulated quote is then also artificially low, so the module's swap executes and accepts a below-fair-value PC output for the user's PRC20/gas-token principal — the difference (up to just under 5% beyond the true price plus arbitrary further depression of `quote` itself since it's unbounded) is captured by the attacker's own pool position through the price impact and reversion, while the protocol-mediated swap silently accepts the bad execution price because it never checks against a reference the attacker cannot move in the same block.

This mirrors the TBY bug's root cause: a value used to convert user input into a protocol-issued payout is taken from a single, unprotected, attacker-influenceable source, and the protocol has no mechanism (time-weighting, external reference, or minimum-quality check) to prevent the attacker from choosing the instant that benefits them.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... or canonical UniversalTx state" and "draining ... protocol-controlled funds" in the allowed impact list: the module executes swaps on behalf of users using a manipulable price, resulting in the recipient (or, via arbitrage, the attacker) receiving fewer PC tokens than the fair-value swap would yield, while the module account absorbs (or the recipient loses) the shortfall. Because these are module-originated `DerivedEVMCall`s moving real protocol-facilitated value (deposited external-chain funds or gas-fee refunds), a successful manipulation results in real economic loss on each affected inbound/outbound.

### Likelihood Explanation
Exploitability requires the attacker to control or heavily influence the relevant Uniswap V3 pool's spot price at the exact block where their own inbound/outbound swap executes. Since the triggering event (their own deposit reaching ballot finalization, or their own outbound being voted OBSERVED) is attacker-initiated and its finalization block is at least partially predictable/controllable by the attacker (they can choose when to submit the last needed observation, or simply wait for a low-liquidity moment), this is a plausible, non-privileged attack, though it requires capital to move the pool and is most profitable against low-liquidity PRC20/WPC pools.

### Recommendation
Do not rely on a single unbounded spot quote for `minPCOut`. Use a TWAP-based quote (e.g., average price over N blocks) from the Uniswap V3 pool, set a non-zero `sqrtPriceLimitX96`, and/or bound the acceptable execution price against `x/uregistry`'s independently-tracked chain/token config rather than deriving the slippage floor solely from the manipulable instantaneous quote.

### Proof of Concept
1. Attacker identifies a low-liquidity Uniswap V3 pool between a PRC20 gas token and WPC that `UniversalCore` uses for auto-swaps.
2. Attacker submits (or already has pending) a `GAS` inbound deposit from an external chain for a meaningful amount of that PRC20 token.
3. Right before the final UV vote finalizes the inbound's ballot (attacker can monitor the mempool/vote count and time their own transaction, or simply retry until conditions align), attacker executes a large swap against the pool to depress the PRC20→WPC spot price.
4. The ballot finalizes; `ExecuteInboundGas` calls `GetSwapQuote` (unprotected spot price) and computes `minPCOut` from the depressed quote; `CallPRC20DepositAutoSwap` executes the swap at the manipulated price, delivering less PC than fair value to the recipient UEA.
5. Attacker reverses their manipulative swap in the same or next block, extracting the value difference via arbitrage while the protocol/user absorbs the loss.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-148)
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
