## Analysis

The Push Chain analog of this bug class lives in the GAS-abstraction auto-swap logic in `x/uexecutor`. Whenever an inbound of type `GAS` / `GAS_AND_PAYLOAD` is finalized, or whenever excess relayer gas is refunded, the module swaps a PRC20 gas token for native PC through the on-chain UniswapV3 `QuoterV2`/`SwapRouter`, using a spot-price quote and a fixed percentage slippage tolerance — structurally identical to the Asymmetry rETH bug (spot-price quote + fixed % slippage, no TWAP, no user-supplied `minOut`).

### Title
Fixed 5% slippage computed from an unprotected UniswapV3 spot-price quote enables sandwich extraction on GAS-abstraction auto-swaps and gas refunds - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`GetSwapQuote` reads the current spot price directly from the UniswapV3 `QuoterV2.quoteExactInputSingle` view call [1](#0-0) , and the caller immediately derives `minPCOut = quote * 95 / 100` before executing the real swap through `CallPRC20DepositAutoSwap` in the same keeper call [2](#0-1) . The same pattern is repeated for `GAS_AND_PAYLOAD` inbounds in `gasAndPayloadDepositAutoSwap` [3](#0-2)  and for excess-gas refunds in `applyGasRefund` [4](#0-3) . There is no TWAP, no oracle cross-check, and no ability for the affected user to supply their own `minOut` — the 5% band is a fixed protocol constant applied to whatever spot price the pool happens to show at execution time.

### Finding Description
The auto-swap flow is triggered deterministically and atomically inside the state transition that finalizes an inbound ballot: once quorum (2/3+) of universal validators submit `MsgVoteInbound`, `VoteInbound` synchronously calls `ExecuteInbound` → `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` in the very same Cosmos transaction that carries the finalizing vote [5](#0-4) . That execution path calls `GetSwapQuote` for a live spot price and then swaps with only a 5% floor [6](#0-5) .

Because the quote and the real swap read the pool state that is mutable by any ordinary EVM transaction on Push Chain (the UniswapV3 pool/router are ordinary deployed contracts, not privileged), an unprivileged actor who observes the pending finalizing vote transaction in the mempool can:
1. Push the PRC20/WPC pool price against the module right before the finalizing vote transaction lands (front-run swap).
2. Let the module's auto-swap execute at the now-worse spot price, still within the 5% band, so it does not revert.
3. Reverse the price manipulation immediately after (back-run swap), capturing the difference as MEV profit extracted from the value that should have gone to the user's UEA (or, on the refund path, from the protocol/relayer's own refunded amount).

This exactly mirrors the referenced report's root cause: "price of [asset] on [DEX] is the spot price and is only determined during the transaction," bounded by a fixed slippage percentage that is materially larger than typical DEX fees, giving a guaranteed profitable sandwich window.

### Impact Explanation
Every GAS and GAS_AND_PAYLOAD inbound, and every successful/failed outbound that triggers a gas refund, routes user or protocol value through this unprotected swap. An unprivileged MEV actor can systematically extract up to ~5% of the swapped notional from ordinary users' gas-abstraction deposits and from gas refunds, corrupting the PRC20/native-asset accounting invariant that the user (or refund recipient) should receive fair value for their bridged/refunded tokens. This is in-scope impact: "corruption of PRC20 or native asset accounting, gas fee accounting, refund accounting ... token mapping" reachable purely from "ordinary user deposits ... or default transaction submission paths alone," with no privileged actor required — the attacker only needs to trade against a public AMM pool.

### Likelihood Explanation
High. The vote-finalization transaction (and thus the exact swap parameters) is visible in the mempool before inclusion, UniswapV3 pools for PRC20/WPC pairs are ordinary permissionless contracts reachable by anyone, and the 5% tolerance is generous compared to normal DEX price impact — well within reach of a single-block sandwich by any address holding modest capital. This applies identically to `ExecuteInboundGas`, `ExecuteInboundGasAndPayload`'s `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`.

### Recommendation
Replace the instantaneous `QuoterV2` spot-price quote with a manipulation-resistant reference price (e.g., a TWAP over the UniswapV3 pool, or the `ChainMeta`/oracle-reported price already voted on-chain by universal validators), and/or tighten the slippage tolerance substantially. Where feasible, let the true beneficiary (or at minimum a protocol-computed fair-value oracle independent of the same-block AMM state) bound `minPCOut`, rather than deriving both the quote and the tolerance from the same manipulable spot price in the same transaction.

### Proof of Concept
1. Attacker monitors the mempool for a `MsgVoteInbound` transaction that will cross the 2/3+ quorum threshold for a `GAS`/`GAS_AND_PAYLOAD` inbound (or for an outbound observation that will trigger `applyGasRefund`).
2. In the block that will include the finalizing vote, attacker submits (front-run) a swap on the PRC20↔WPC UniswapV3 pool used by `GetSwapQuote`/`GetDefaultFeeTierForToken` to move the spot price unfavorably for the upcoming module swap direction.
3. The finalizing `MsgVoteInbound` executes `ExecuteInboundGas`, which calls `GetSwapQuote` against the now-manipulated pool and computes `minPCOut = quote*95/100` [7](#0-6) , then executes `CallPRC20DepositAutoSwap` which succeeds since it only needs to clear the depressed `minPCOut` floor.
4. Attacker back-runs with the reverse swap, restoring the pool price and realizing the price-impact difference as profit, extracted from the value that should have reached the recipient UEA (or the gas-refund recipient).

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
