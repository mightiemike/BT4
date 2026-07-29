This confirms the analog: `MsgVoteInbound` deterministically triggers `k.ExecuteInbound(ctx, utx)` synchronously the moment the 2/3 quorum vote lands [1](#0-0) , and this quorum-crossing vote is public, predictable mempool activity, giving an attacker a reliable trigger to sandwich the on-chain Uniswap V3 pool used for the auto-swap.

### Title
Sandwichable auto-swap slippage protection derived from live, manipulable QuoterV2 quote on GAS/GAS_AND_PAYLOAD inbound execution and gas refunds - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
Push Chain's `x/uexecutor` module computes `minPCOut` slippage protection for on-chain PRC20→WPC swaps by calling `QuoterV2.quoteExactInputSingle` immediately before executing the swap, then applying a fixed 5% haircut to that live quote. Because the quote reflects the *current, attacker-observable* pool state rather than an externally-anchored or time-weighted reference price, an attacker can manipulate the underlying Uniswap V3 pool immediately around the deterministic, publicly-predictable transaction that triggers the swap, extracting value from the swap at the expense of the depositing user or the protocol's gas-refund recipient — the exact bug class described in the referenced report (dynamic slippage bound calculated from the same manipulable on-chain state as the swap itself).

### Finding Description
Three code paths compute `minPCOut` the same way — fetch a live quote, then apply `quote * 95 / 100`:

1. `ExecuteInboundGas` (GAS inbound route): after quorum, calls `GetSwapQuote` then `CallPRC20DepositAutoSwap` with `minPCOut = quote*95/100` [2](#0-1) .
2. `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound route): identical pattern [3](#0-2) .
3. `applyGasRefund` (outbound gas refund, swap leg): identical pattern for `refundUnusedGas` with `withSwap=true` [4](#0-3) .

`GetSwapQuote` itself is a plain `CallEVM` static call into `QuoterV2.quoteExactInputSingle`, which internally simulates the swap against the pool's *current* tick/liquidity state (the on-chain analog of `slot0()`) [5](#0-4) . There is no TWAP, no externally-supplied reference price, and no check against a price recorded before the triggering event was observed.

Crucially, the transaction that performs this quote+swap is not attacker-submitted directly, but its timing is fully predictable: `VoteInbound` executes `ExecuteInbound` synchronously the instant the 2/3 quorum vote lands [1](#0-0) , and quorum-crossing `MsgVoteInbound` transactions from Universal Validators are ordinary, publicly observable mempool transactions. Any unprivileged actor watching the mempool (or simply the depositor themselves, who knows their own deposit is en route to quorum) can submit a transaction that moves the PRC20/WPC pool price immediately before the quorum-finalizing block, and a reverse transaction immediately after, sandwiching the module's swap. Because `minPCOut` is derived from the same manipulated pool state at execution time, the 95%-of-quote check will always pass trivially — it bounds nothing against an attacker who can move the price within the 5% band (or more, in thin pools), extracting the difference as MEV at the expense of the swap's true output (funds destined for the user's UEA in the GAS/GAS_AND_PAYLOAD case, or the gas-refund recipient in the `applyGasRefund` case).

### Impact Explanation
This falls under "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting" in the allowed-impact list. An unprivileged attacker can extract value from ordinary users' gas-topup/GAS_AND_PAYLOAD deposits and from gas-refund swaps by sandwiching the pool around the deterministic, publicly-observable quorum-finalizing vote, reducing the amount of native PC value the depositor or refund recipient actually receives versus fair market price. The magnitude scales with pool liquidity depth — for newly listed or thinly-liquid PRC20/WPC pools the loss can exceed trivial dust and is a repeatable, reachable "no privileged actor" exploitation path with an honest validator set and honest nodes.

### Likelihood Explanation
Likelihood is high for any PRC20 with a shallow WPC pool: the triggering vote (crossing 2/3 UV quorum) is not secret and is broadcast like any Cosmos tx before inclusion; an attacker only needs a wallet capable of swapping on the same Uniswap V3 pool used by `UniversalCore` and enough capital to move price within the pool's depth. No governance, validator, or TSS compromise is required — this is purely a function of the on-chain slippage-bound design being derived from the same manipulable state it is meant to protect against.

### Recommendation
Do not derive `minPCOut` from a live `QuoterV2` quote fetched in the same flow as the swap. Instead:
- Use a Uniswap V3 TWAP (time-weighted average price) observation window that predates the triggering event (e.g., anchored to the inbound's finalization block minus N blocks) so the reference price cannot be moved atomically around the triggering transaction.
- Alternatively, have the reference/expected output amount supplied and locked in at the time the inbound was first observed/voted (before quorum), rather than recomputed live at execution time, and validate the final swap output against that earlier-committed value.
- Consider widening protections with a maximum allowed price-impact check independent of the quote itself, and/or routing large swaps through smaller tranches to limit sandwich profitability.

### Proof of Concept
1. Attacker observes (via public mempool/RPC) that a `MsgVoteInbound` for a GAS or GAS_AND_PAYLOAD inbound is about to reach 2/3 quorum (e.g., the attacker is the depositor and knows their own inbound is en route, or watches UV vote submissions).
2. Immediately before the quorum-finalizing `MsgVoteInbound` transaction is included, attacker submits a large swap on the PRC20/WPC Uniswap V3 pool used by `UniversalCore`, pushing the pool price against the upcoming module-initiated swap direction.
3. The quorum-finalizing transaction executes `k.ExecuteInbound` → `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`, which calls `GetSwapQuote` (reflecting the manipulated price) and computes `minPCOut = quote*95/100` [6](#0-5) , then executes `CallPRC20DepositAutoSwap` against the still-manipulated pool — the check trivially passes since it was computed from the same manipulated state.
4. Attacker immediately reverses their swap, capturing the price-impact spread that would otherwise have gone to the depositing user as PC value, up to the 5% slippage band (more in thin pools since the bound is elastic, not absolute).

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L148-155)
```go
	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L364-379)
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
}
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
