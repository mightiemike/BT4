Confirmed: `ExecuteInboundGas` runs synchronously inside `VoteInbound` (called from `MsgVoteInbound` handler), which is an ordinary transaction included in a block by the proposer/mempool ordering — nothing prevents an unprivileged attacker from placing swap transactions immediately before and after the vote-finalizing transaction in the same block to manipulate the Uniswap V3 pool that `GetSwapQuote`/`CallPRC20DepositAutoSwap` read from. [1](#0-0) [2](#0-1) 

### Title
On-chain QuoterV2 quote used as slippage reference for auto-swap deposits/refunds enables sandwich-attack value extraction from user funds - (File: x/uexecutor/keeper/execute_inbound_gas.go, execute_inbound_gas_and_payload.go, outbound.go)

### Summary
The `x/uexecutor` module's gas-abstraction auto-swap and gas-refund paths compute `minPCOut` (or `minPCOut` for token swaps) by calling `GetSwapQuote`, which reads the current Uniswap V3 `QuoterV2.quoteExactInputSingle` price on-chain, then applies a flat 5% slippage tolerance before executing the swap via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. This is the same anti-pattern flagged in the external report: deriving the minimum-output guard from a spot on-chain quote taken immediately before the swap gives no protection against price manipulation that occurs around (not merely inside) that same window, because the quote and the swap execute as two separate, sequential EVM calls inside the handling of an ordinary user-visible transaction (`MsgVoteInbound` / outbound-vote finalization) whose ordering within a block is attacker-influenceable.

### Finding Description
`GetSwapQuote` performs a static `CallEVM` (commit=false) against the `QuoterV2` contract to fetch the current expected output for the PRC20→WPC (or gas-token→WPC) swap: [3](#0-2) 

This quote is then discounted by a fixed 5% and passed as `minPCOut` into `CallPRC20DepositAutoSwap`, which performs the actual on-chain swap via `depositPRC20WithAutoSwap`: [4](#0-3) 

The identical pattern is repeated in `gasAndPayloadDepositAutoSwap` for `GAS_AND_PAYLOAD` inbounds: [5](#0-4) 

and in `applyGasRefund`, which swaps the leftover gas token back to PC when an outbound completes: [6](#0-5) 

All three call sites are only reachable when a `MsgVoteInbound` (for `GAS`/`GAS_AND_PAYLOAD` inbounds) or `MsgVoteOutbound` (for gas refunds) transaction reaches quorum and is processed as an ordinary Cosmos transaction: [1](#0-0) 

Because these transactions are ordinary, mempool-visible Cosmos transactions whose relative ordering within a block is determined by the proposer (typically by gas price / fee priority, not by any privileged role), an unprivileged attacker can:
1. Observe a pending `MsgVoteInbound`/`MsgVoteOutbound` transaction that will trigger the finalizing vote (the last vote needed to cross the 2/3 threshold is visible in the mempool, and the resulting auto-swap amount is derivable from the inbound/outbound data).
2. Submit a transaction with higher fee/priority that trades against the same Uniswap V3 pool (`prc20`/gas-token ↔ `WPC`) to move the price against the protocol's upcoming swap, placed immediately before the finalizing vote transaction in the same block.
3. Let the module's `GetSwapQuote` read the now-manipulated pool price, compute `minPCOut = quote * 95%` from that manipulated price (which still "protects" against further movement from that already-bad reference point), and execute the swap.
4. Submit a back-run transaction immediately after in the same block to restore the pool price and capture the value extracted from the user's/protocol's swap.

The 5% slippage tolerance does not protect against this because the reference price itself (the quote) is fetched *after* the attacker's manipulating trade has already been applied to pool state — exactly analogous to the harvest bug in the external report, where `getSwapOutput` returned a quote already subject to manipulation before `swap` used it as the floor.

### Impact Explanation
Each `GAS`/`GAS_AND_PAYLOAD` inbound execution and each successful/failed outbound with excess gas triggers one of these auto-swaps, moving real user/protocol-bridged value (PRC20 gas tokens) through a public, sandwich-able AMM pool with a floor derived from a manipulable spot price. An attacker can systematically extract value from every gas-abstraction inbound and every gas refund, resulting in recurring, protocol-wide loss of user-bridged funds — a "stealing/draining of user or protocol-controlled funds" impact reachable purely through unprivileged transaction submission (fee-based ordering), not any validator or admin privilege.

### Likelihood Explanation
Likelihood is Medium-High: sandwiching AMMs via fee-priority transaction ordering is a well-understood, commonly automated MEV technique requiring no special access to Push Chain's validator set, TSS, or admin keys — only the ability to submit ordinary EVM transactions against the same Uniswap V3 pool the module swaps through, and visibility into pending `MsgVoteInbound`/`MsgVoteOutbound` transactions (which reveal the swap amount and token in advance). Every `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound with a gas refund is a candidate target, making this a recurring rather than one-off exposure.

### Recommendation
- Do not derive `minPCOut` purely from a same-transaction on-chain quote with a fixed percentage slippage. Consider using a manipulation-resistant reference price (e.g., a TWAP oracle, or the chain-meta/gas-price oracle already aggregated via `MsgVoteChainMeta` from multiple Universal Validators) to bound acceptable swap output, rather than trusting the instantaneous `QuoterV2` spot quote.
- Alternatively, widen slippage tolerance is not a fix; instead cap the maximum per-swap notional routed through the AMM, or route gas-abstraction/refund swaps through a mechanism that isn't influenced by same-block attacker-controlled trades (e.g., a delayed/batched execution window, or a price sanity check against the independently-aggregated `ChainMeta`/gas-price oracle before allowing the swap to proceed).
- Consider rejecting or flagging swaps where the fetched quote deviates significantly from the recent aggregated chain-meta price, reverting to the no-swap fallback path (which already exists in `applyGasRefund`) in that case.

### Proof of Concept
1. Attacker monitors the Push Chain mempool for a `MsgVoteInbound` transaction that will supply the final vote needed to reach 2/3 quorum for a `GAS` or `GAS_AND_PAYLOAD` inbound with a known bridged `amount` and `prc20` token (derivable from the inbound's `AssetAddr`/`TokenConfig`).
2. Attacker submits, with higher gas price/priority, a large swap on the same PRC20↔WPC Uniswap V3 pool (buying WPC / selling PRC20) to depress the PRC20→WPC exchange rate, ordered immediately before the target `MsgVoteInbound` transaction in the same block.
3. The proposer orders transactions by fee priority; the module's `GetSwapQuote`/`CallPRC20DepositAutoSwap` sequence in `ExecuteInboundGas` executes against the manipulated pool state, computing `minPCOut` from the depressed quote and completing the swap at the manipulated (bad) rate.
4. Attacker submits a back-run transaction immediately after in the same block to reverse their initial trade, realizing the price-impact loss extracted from the protocol's auto-swap as profit.
5. Repeat for every subsequent `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound gas refund, since the pattern is identical (`k.applyGasRefund` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas`).

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L104-153)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L213-237)
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
	} else {
		swapFallbackReason = fmt.Sprintf("fee tier fetch failed: %s", swapErr.Error())
	}
```
