## Analysis: Spot-Price Slippage Protection Enables Sandwich Extraction on Auto-Swap/Refund Paths

The report describes an attacker who manipulates a live AMM price and exploits the fact that the protocol derives a critical threshold (health) from that same manipulable price without protection against manipulation within the transaction window. Push Chain's Universal Executor has a structurally identical pattern: it computes `minPCOut` slippage protection from a **live, same-call Uniswap V3 spot quote** with no TWAP or external reference price, then immediately executes the swap.

### Title
Same-block spot-price quote used as slippage protection for protocol-initiated PRC20→WPC swaps enables sandwich extraction on gas auto-swap and refund paths - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Finding Description
For `TxType_GAS` and `TxType_GAS_AND_PAYLOAD` inbounds, the module deposits the user's synthetic PRC20 gas token and immediately swaps it to native PC via `CallPRC20DepositAutoSwap`. The swap's slippage bound is computed as: [1](#0-0) 

`quote` comes from `GetSwapQuote`, which calls the Uniswap V3 `QuoterV2.quoteExactInputSingle` against the live pool state in the same keeper call, right before the swap executes: [2](#0-1) 

The same pattern is used for excess-gas refunds on outbound finalization in `applyGasRefund` / `getSwapQuoteForRefund`: [3](#0-2) [4](#0-3) 

There is no TWAP, no external chain-meta price cross-check, and no oracle bound — the "slippage protection" (`quote * 95/100`) is derived from the exact pool state the swap is about to execute against, in the same call. This is analogous to the report's core flaw: the protocol computes a safety threshold from a price source the attacker can move immediately beforehand, so the threshold offers no real protection — it simply legitimizes whatever price the attacker has already set.

Because the underlying pool (PRC20 gas-token / WPC Uniswap V3 pool) is a standard permissionless AMM, any unprivileged actor can trade against it. An attacker can:
1. Swap into the pool to push the PRC20→WPC price down immediately before the module's `depositPRC20WithAutoSwap` or `refundUnusedGas(withSwap=true)` call lands (same block / adjacent transaction the attacker can predict since gas-inbound execution and outbound-vote finalization follow deterministic, observable triggers).
2. The module then fetches `quote` from the now-depressed price and computes `minPCOut = quote * 0.95`, which is trivially satisfied even though the executed swap converts the user's/protocol's PRC20 at a manipulated, unfavorable rate.
3. The attacker reverses their trade afterward, capturing the value difference the user (gas-inbound recipient) or refund recipient should have received.

### Impact Explanation
This corrupts gas fee accounting and refund accounting exactly as the "Required Impacts" scope calls out — legitimate users receive less native PC than fair market value from `CallPRC20DepositAutoSwap` (gas top-up) and `CallUniversalCoreRefundUnusedGas` (excess-gas refund), while the attacker extracts the difference via the pool trade. This is a reachable, unprivileged-user-triggerable value leak on two production execution paths (`ExecuteInboundGas`/`ExecuteInboundGasAndPayload` and outbound refund) that both use protocol/module funds and honest-validator-driven finalization, not any malicious validator or relayer behavior.

### Likelihood Explanation
Every `GAS` / `GAS_AND_PAYLOAD` inbound and every outbound with an excess-gas refund invokes this exact same-block quote → 95%-slippage → execute pattern. The trigger points (inbound vote finalization, outbound vote finalization) are public, observable on-chain events, making sandwiching practically feasible for any actor watching the mempool/finalization flow with capital in the target pool.

### Recommendation
Do not derive slippage protection solely from a spot quote fetched in the same transaction as execution. Use a TWAP-based quote (e.g., Uniswap V3 `OracleLibrary`/observations over a meaningful window), or bound the acceptable price against the chain-meta/oracle-tracked reference price already maintained by the protocol, or apply a maximum single-block price-impact check independent of the just-fetched quote, so that an attacker cannot set the reference price and the acceptance threshold in the same breath.

### Proof of Concept
1. Attacker identifies a pending `GAS`/`GAS_AND_PAYLOAD` inbound (or a pending outbound about to be finalized with excess gas) whose swap uses `PRC20_gasToken -> WPC` on a known Uniswap V3 pool.
2. Attacker submits a large swap that pushes the pool price of `PRC20_gasToken` down relative to `WPC`.
3. In the same/adjacent block, the validator-submitted `MsgVoteInbound`/`MsgVoteOutbound` finalizes and triggers `GetSwapQuote` → `CallPRC20DepositAutoSwap` (or `CallUniversalCoreRefundUnusedGas` with swap), which reads the now-depressed price and computes `minPCOut` from it — trivially satisfied by the depressed execution.
4. Attacker reverses their initial swap, extracting the price-impact difference as profit, while the intended recipient receives less PC than the pre-manipulation fair value.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-146)
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

**File:** x/uexecutor/keeper/outbound.go (L259-269)
```go
// getSwapQuoteForRefund fetches a Uniswap quote for the gas token refund swap.
func (k Keeper) getSwapQuoteForRefund(ctx sdk.Context, gasToken common.Address, fee *big.Int, amount *big.Int) (*big.Int, error) {
	quoterAddr, err := k.GetUniversalCoreQuoterAddress(ctx)
	if err != nil {
		return nil, err
	}
	wpcAddr, err := k.GetUniversalCoreWPCAddress(ctx)
	if err != nil {
		return nil, err
	}
	return k.GetSwapQuote(ctx, quoterAddr, gasToken, wpcAddr, fee, amount)
```
