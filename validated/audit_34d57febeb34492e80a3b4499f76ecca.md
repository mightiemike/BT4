## Title
Hardcoded 5% slippage tolerance on `GAS`/`GAS_AND_PAYLOAD` autoswap deposits and gas refunds enables front-running/sandwich attacks that steal value from bridging users — (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The Beedle report's bug class is: a protocol-level execution parameter (auction length) is fixed by a counterparty without giving the affected party any way to set a safe minimum, and an unprivileged actor can exploit the timing gap between "parameter set" and "parameter used" to seize value. In Push Chain, `x/uexecutor`'s `GAS`/`GAS_AND_PAYLOAD` inbound handling and outbound gas-refund handling perform on-chain Uniswap V3 swaps (PRC20 → WPC) using a **quote fetched and consumed atomically at execution time**, with a **hardcoded, non-configurable 5% slippage tolerance**. Because the deposit-triggering event (the UV vote that reaches quorum) is publicly observable in the mempool before it lands, and the slippage band is generous and fixed rather than derived from trade size or user preference, any unprivileged trader can manipulate the on-chain WPC/PRC20 pool price immediately around that block to sandwich the swap and capture the difference, at the direct expense of the bridging user's minted/refunded value.

### Finding Description
For `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbounds, once UV votes reach quorum the keeper deposits the bridged PRC20 with an automatic swap into WPC: [1](#0-0) 

The quote is fetched from the live Uniswap V3 `QuoterV2` contract and immediately used to compute `minPCOut` with a fixed 5% slippage tolerance, then passed straight into the swap call in the same keeper invocation: [2](#0-1) 

The same pattern (`GetSwapQuote` → `quote * 95 / 100` → `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`) is used both for deposits and for excess-gas refunds on outbound completion: [3](#0-2) [4](#0-3) 

Key facts that make this reachable by an unprivileged attacker:
- `GetSwapQuote`/`GetDefaultFeeTierForToken` read live, unauthenticated AMM state (`QuoterV2.quoteExactInputSingle`) with no TWAP or oracle cross-check.
- The 5% band is a hardcoded constant across all token pairs and trade sizes — there is no way for the bridging user (borrower analog) to request a tighter slippage bound, and no floor tied to expected pool depth (auction-length analog: fixed value chosen by the protocol, not the affected party).
- The triggering transaction (the UV vote that flips quorum and fires the deposit) is visible in the mempool ahead of execution, giving any unprivileged trader the ability to move the PRC20/WPC pool price just before that block and reverse the move just after — a classic sandwich — capturing up to the full 5% band at the depositor's/refund-recipient's expense.
- On swap failure the code falls back to a no-swap deposit, but on a manipulated-but-nominally-successful swap (price still inside the 5% band) there is no revert and no protection: the user simply receives less PC than a fair quote would have produced.

### Impact Explanation
This directly causes unauthorized value loss for bridging users: the PRC20-to-PC (WPC) conversion executed on their behalf systematically returns less native value than a fair-market quote would, siphoned to an unprivileged MEV actor. This is a corruption of PRC20/native asset accounting and gas-refund accounting reachable purely through ordinary user deposits and default transaction submission paths — no privileged validator, relayer, or admin action is required, matching the required in-scope impact of "corruption of PRC20 or native asset accounting ... refund accounting."

### Likelihood Explanation
Every `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound gas refund goes through this exact code path, and the finalizing UV votes are visible before quorum is reached, giving predictable timing for a sandwich. The fixed 5% band is large relative to typical liquid-pool price impact, making the attack economically attractive whenever pool depth is thin relative to bridge volume (a very common condition for newly listed PRC20/WPC pairs). No special permissions or validator collusion are needed.

### Recommendation
- Replace the fixed 5% slippage constant with a slippage bound derived from a manipulation-resistant reference (e.g., TWAP over a window, or a chain-meta-oracle price) rather than a single spot quote fetched and consumed atomically.
- Consider tightening the tolerance and/or scaling it with trade size/pool depth so it approximates real execution risk instead of a blanket allowance.
- Where feasible, let users (or the protocol via governance) express a tighter maximum acceptable slippage for large deposits/refunds, and revert (rather than silently accept a worse price) when the live quote deviates materially from a recent reference price.
- Add pre-trade/post-trade price-impact sanity checks (e.g., compare `GetSwapQuote` output against the chain-meta gas-price oracle or a stored recent quote) to detect and reject clearly manipulated pool states.

### Proof of Concept
1. Attacker (unprivileged) monitors the Push Chain mempool for `MsgVoteInbound`/`MsgVoteOutbound` transactions that will reach quorum and trigger `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`/`applyGasRefund`.
2. Immediately before that block, attacker submits a large swap on the PRC20/WPC Uniswap V3 pool to push the spot price against the pending deposit direction.
3. The quorum-finalizing transaction executes `GetSwapQuote` and `CallPRC20DepositAutoSwap` with `minPCOut = quote * 95 / 100` computed from the now-manipulated price — see [5](#0-4) . The swap succeeds because it is still within the wide 5% tolerance of the manipulated price.
4. Attacker immediately reverses the swap in a following block, extracting the price impact as profit while the bridging user's UEA received materially less PC/WPC than fair value.

### Citations

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
