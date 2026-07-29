### Title
Self-referential on-chain slippage quote enables sandwich extraction of bridged funds and gas refunds in gas-abstraction auto-swap and refund flows - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The reported bug class (`withdraw_liquidity` lacking user-specified slippage protection) maps onto Push Chain's gas-abstraction swap flow. Push Chain does implement a `minPCOut` slippage bound before every module-originated PRC20→PC swap, but that bound is derived from an on-chain AMM quote fetched via `GetSwapQuote` immediately before the swap in the *same* transaction, rather than from a user-supplied minimum or a manipulation-resistant price source. Because the module always applies a fixed 5% band around whatever price the pool currently reports, an unprivileged attacker who can move the pool price in the same block the swap lands (via an ordinary swap transaction on the shared Uniswap-style pool) can force bridged user funds and protocol gas-refunds to convert at an attacker-favorable price and capture the difference — with no way for the affected party to set a real minimum.

### Finding Description
Three module code paths perform a "quote-then-swap" pattern where both the quote and the swap execute inside the same keeper call, sourced from live pool reserves:

- `ExecuteInboundGas` (gas abstraction inbound): fetches `quote` via `GetSwapQuote` then computes `minPCOut = quote * 95 / 100` and immediately calls `CallPRC20DepositAutoSwap`. [1](#0-0) 

- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound path) does the identical pattern. [2](#0-1) 

- `applyGasRefund` (outbound gas-refund path) repeats the same pattern when swapping the leftover gas token back to PC for the recipient. [3](#0-2) 

`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` against the live pool state at the moment of execution — it is not a TWAP, oracle, or user-committed price: [4](#0-3) 

Because inbound execution is triggered synchronously the instant a `MsgVoteInbound` ballot crosses quorum (`VoteInbound` → `ExecuteInbound` in the same transaction, in the same block as the finalizing UV vote), and `MsgVoteOutbound` similarly triggers `FinalizeOutbound`/`applyGasRefund` synchronously on the finalizing vote, the exact block in which the swap will execute is deterministically knowable in advance from the public mempool (UV votes are ordinary, publicly broadcast Cosmos transactions). An unprivileged attacker can: [5](#0-4) [6](#0-5) 

1. Observe the finalizing `MsgVoteInbound`/`MsgVoteOutbound` in the mempool (or simply target predictable retry/relayer behavior).
2. Submit an ordinary swap against the same Uniswap-style pool (front-run) to push the pool price in the direction that lowers `quoteExactInputSingle`'s output for the module's upcoming swap.
3. Let the module's swap execute — because `minPCOut` is derived from the *already-manipulated* quote, the manipulated price is itself accepted as "acceptable," so the swap clears at the attacker-favorable price rather than reverting.
4. Reverse the initial swap (back-run) to realize the extracted value, which comes at the direct expense of the bridging user's converted gas amount (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`) or the protocol/recipient's gas refund (`applyGasRefund`).

This is the same root cause the external report flags for `withdraw_liquidity`: the amount a party receives on conversion is determined solely by a same-transaction, unauthenticated on-chain price with no independently supplied minimum-acceptable-output from the party whose funds are at risk. The fixed 5% band does not fix this — it only bounds the *additional* slack beyond whatever price the attacker has already set, not against manipulation of the reference price itself.

### Impact Explanation
Funds converted through the gas-abstraction swap (inbound gas top-ups) and through excess-gas refunds after outbound execution are both auto-swapped using a manipulable, self-referential price reference with no floor tied to fair market value. This allows an unprivileged actor to systematically extract value from ordinary users' bridged deposits and from protocol-managed refund flows, i.e. "draining of user or protocol-controlled funds" reachable via ordinary swap transactions and default vote-triggered execution, with honest validators and honest nodes.

### Likelihood Explanation
UV votes are broadcast as regular Cosmos transactions and are visible pre-confirmation; the quorum-crossing vote is identifiable, and the resulting swap size (the inbound `amount` / refund `refundAmount`) is known from the inbound/outbound record. Any actor able to trade on the underlying pool (no privileged role required) can attempt the sandwich. Success depends on pool liquidity/depth and same-block execution feasibility, which is realistic on most EVM-compatible consensus with public mempools.

### Recommendation
Do not derive `minPCOut` solely from a quote fetched inside the same execution as the swap. Options: use a manipulation-resistant reference (e.g., TWAP oracle) for the floor, cap price deviation against a recent trusted reference rather than the instantaneous quote, or add a maximum-price-impact circuit breaker that reverts (and reverts/queues instead of accepting) if the realized price deviates materially from a longer-window reference, rather than always accepting whatever the just-fetched spot quote allows minus 5%.

### Proof of Concept
1. Attacker monitors the mempool for `MsgVoteInbound` (or `MsgVoteOutbound`) transactions that will push a ballot to quorum for an inbound gas-abstraction swap of known `amount`.
2. Attacker submits (or has included in the same block, prior to the finalizing vote) a large swap on the PRC20↔WPC pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, moving the pool price unfavorably for the upcoming module swap.
3. The finalizing vote lands, `ExecuteInboundGas` runs `GetSwapQuote` against the now-manipulated pool and computes `minPCOut` from that manipulated quote, then calls `CallPRC20DepositAutoSwap`, which executes at the manipulated price (still within the accepted 5% band). [7](#0-6) 
4. Attacker reverses their initial swap, capturing the price impact they created at the expense of the value the bridging user (or refund recipient) received.

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

**File:** x/uexecutor/keeper/outbound.go (L71-97)
```go
func (k Keeper) FinalizeOutbound(ctx context.Context, utxId string, outbound types.OutboundTx) error {
	// If not observed yet, do nothing
	if outbound.OutboundStatus != types.Status_OBSERVED {
		return nil
	}

	obs := outbound.ObservedTx
	if obs == nil {
		return nil
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	k.Logger().Info("finalizing outbound",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"success", obs.Success,
		"dest_chain", outbound.DestinationChain,
		"tx_type", outbound.TxType.String(),
	)

	if !obs.Success {
		return k.handleFailedOutbound(sdkCtx, utxId, outbound, obs)
	}

	return k.handleSuccessfulOutbound(sdkCtx, utxId, outbound, obs)
}
```

**File:** x/uexecutor/keeper/outbound.go (L213-231)
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
