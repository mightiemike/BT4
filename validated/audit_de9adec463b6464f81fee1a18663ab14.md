Confirmed: `GetSwapQuote` (in `x/uexecutor/keeper/evm.go`) calls `QuoterV2.quoteExactInputSingle` directly against the live Uniswap V3 pool, and `ExecuteInboundGas` (in `x/uexecutor/keeper/execute_inbound_gas.go`) immediately derives `minPCOut = quote * 95 / 100` and executes the swap via `CallPRC20DepositAutoSwap` in the same message handling, with no TWAP or oracle cross-check.

### Title
Hardcoded 5% slippage on spot-price Uniswap V3 quote enables sandwich extraction of user gas-abstraction deposits, depegging PRC20/PC value received - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
When an ordinary user bridges native gas funds into Push Chain (`TxType_GAS` inbound, and similarly the `GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD`/CEA autoswap variants), `ExecuteInboundGas` fetches a swap quote via `GetSwapQuote` — a direct, uncached call to `QuoterV2.quoteExactInputSingle` on the live Uniswap V3 pool — then computes `minPCOut` as a fixed 95% of that instantaneous quote and immediately performs the swap through `CallPRC20DepositAutoSwap`. Because the quote source is the pool's current spot price rather than a time-weighted or oracle-anchored price, and the tolerance band is a fixed 5% regardless of pool depth or trade size, any unprivileged user with EVM access to Push Chain can manipulate the PRC20/WPC pool price immediately before the module's swap executes and profit at the depositing user's expense, while the depositing user (and the protocol's supposed 1:1 backing assumption between bridged asset value and the PC/PRC20 the user actually receives) suffers value loss — the practical analog of the reported ETH-slashing depeg: the exchange rate the system assumes (spot quote ≈ fair value) diverges from reality with no protective mechanism.

### Finding Description [1](#0-0) 
`GetSwapQuote` performs a `CallEVM` (`commit=false`) to `QuoterV2.quoteExactInputSingle`, which simulates a swap against the *current* reserves/sqrtPrice of the on-chain Uniswap V3 pool — this is a spot-price read, trivially manipulable within the same or an adjacent block by anyone able to submit ordinary EVM swap transactions against the same pool (no special privilege, no compromised validator or TSS key needed). [2](#0-1)  then immediately uses that spot quote to derive `minPCOut = quote*95/100` and calls `CallPRC20DepositAutoSwap`, which drives `depositPRC20WithAutoSwap` on `UniversalCore` — an on-chain AMM swap executed with only a static 5% slippage floor. The same pattern recurs in `x/uexecutor/keeper/execute_inbound_gas_and_payload.go` (`gasAndPayloadDepositAutoSwap`) and in `x/uexecutor/keeper/outbound.go`'s `applyGasRefund` (`getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas` with `withSwap=true`), both of which follow the identical quote-then-swap-in-same-call pattern with the identical fixed 95% tolerance.

There is no invariant tying the swapped-out PC amount to any external reference price, no per-trade-size dynamic slippage bound, and no minimum liquidity/depth check on the pool before trusting its spot price. A user's own deposit — routed automatically by protocol logic, not the user's own transaction — can be executed at a price up to 5% worse than fair value, and that 5% is a floor an attacker can reliably capture via sandwiching (buy WPC/sell PRC20 before the module's swap to move the price down, then reverse after), extracting value from either the depositing user's expected proceeds or the protocol's backing pool, repeatedly, across every gas-abstraction inbound.

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting" and "unauthorized... refund" impact classes: value that should back the user's UEA 1:1 (in PC-equivalent terms) after autoswap is instead reduced by attacker-captured slippage on every affected inbound, with no burn/reconciliation or price-sanity mechanism analogous to what the external report's mitigation recommended for the ETH-slashing case. Because gas-abstraction deposits are a routine, frequently-triggered user flow (any inbound with `TxType_GAS`, `GAS_AND_PAYLOAD`, or gas refunds on outbound), the aggregate value that can be siphoned from ordinary users/protocol liquidity over time is material, not a one-off griefing case.

### Likelihood Explanation
High: no privileged access, validator collusion, or external chain compromise is required — only the ability to submit ordinary swap transactions against the same Uniswap V3 pool that `UniversalCore` uses for gas-token↔WPC conversion, timed around the predictable execution of `ExecuteInboundGas`/`applyGasRefund`. This is a well-known MEV/sandwich pattern against on-chain AMMs using spot quoters with static slippage tolerances.

### Recommendation
- Replace the direct spot-price `quoteExactInputSingle` call with a TWAP-based or otherwise manipulation-resistant price source before computing `minPCOut`.
- Make the slippage tolerance dynamic based on trade size relative to pool liquidity, or configurable per-token rather than a hardcoded 95%.
- Consider batching/delaying autoswaps or adding a circuit breaker that reverts to the no-swap path (already present as a fallback in `applyGasRefund`) whenever the spot quote deviates materially from a recent reference price.

### Proof of Concept
1. Attacker monitors the mempool/observes a pending `MsgVoteInbound` reaching quorum for a `TxType_GAS` (or `GAS_AND_PAYLOAD`) inbound.
2. Immediately before the vote-finalizing transaction is included, attacker submits an ordinary EVM swap against the PRC20↔WPC Uniswap V3 pool referenced by `UniversalCore`, moving the spot price against the upcoming module swap direction.
3. When `ExecuteInboundGas` executes in the same or next block, `GetSwapQuote` returns the manipulated spot price; `minPCOut` (95% of that already-worse price) is easily satisfied, so `CallPRC20DepositAutoSwap` executes at the manipulated rate.
4. Attacker reverses their position immediately after, capturing the price difference; the depositing user's UEA receives materially less PC-equivalent value than fair-market conversion would provide, and the process repeats on every subsequent gas-abstraction inbound or gas refund. [3](#0-2)

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-148)
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
