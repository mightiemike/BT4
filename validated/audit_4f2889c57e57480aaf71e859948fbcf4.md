This confirms the analysis. The Push Chain node has genuinely public, permissionlessly-tradable Uniswap V3 pools (WPC/PRC20, seeded with real liquidity via `create-pool`) that back the `depositPRC20WithAutoSwap` auto-swap path used on every `GAS` and `GAS_AND_PAYLOAD` inbound. The `minPCOut` protection uses only a live spot-price quote (`GetSwapQuote` → `QuoterV2.quoteExactInputSingle`, no TWAP) with a flat 5% slippage band computed and consumed atomically inside the same keeper call [1](#0-0) , and the pools are attacker-tradable third-party AMM pools deployed with modest seeded liquidity (e.g. "1"/"4" token/WPC units in local setup) [2](#0-1) .

### Title
Spot-price-only slippage protection on protocol-driven autoswap allows AMM price manipulation to steal value from every GAS/GAS_AND_PAYLOAD inbound - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go)

### Summary
`ExecuteInboundGas` / `ExecuteInboundGasAndPayload` / `applyGasRefund` protect the module-driven `depositPRC20WithAutoSwap` / `refundUnusedGas(withSwap=true)` calls with a `minPCOut` derived solely from an instantaneous Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote, discounted by a flat 5% [3](#0-2) [4](#0-3) . This is the same class of bug flagged in the AirPuff report: an unauthenticated actor can manipulate the AMM's instantaneous price before the protected trade executes, and a fixed percentage band on top of a manipulable spot price does not prevent extraction, it just sets the maximum "haircut" the attacker can take per trade.

### Finding Description
The protocol's WPC/PRC20 Uniswap V3 pools are ordinary, permissionless AMM pools that anyone can trade against (they are deployed with real, often thin, liquidity, e.g. `1` token / `4` WPC for most assets in local test config, and presumably comparably modest liquidity in production) [2](#0-1) . Whenever a `GAS` or `GAS_AND_PAYLOAD` inbound finalizes (deterministically, once 2/3+ UV votes are tallied — a publicly observable, predictable event via `PendingInbounds`), the keeper:
1. Fetches a spot quote via `GetSwapQuote` → `quoteExactInputSingle` (no TWAP, `sqrtPriceLimitX96=0`) [4](#0-3) .
2. Computes `minPCOut = quote * 95 / 100` [3](#0-2) .
3. Immediately executes the real swap via `CallPRC20DepositAutoSwap` with that `minPCOut` [5](#0-4) .

Because the pool's spot price is exactly what's manipulated by an unprivileged trader in a prior block (a standard "distort-the-oracle-then-let-the-victim-trade" pattern — here the "victim" is the protocol itself, executing an unavoidable, publicly-telegraphed swap), the quote fetched in step 1 already reflects the attacker's skew. The 5% band only bounds *additional* slippage during the same block, not the pre-existing manipulation baked into the quote. An attacker can push the pool price down (sell PRC20/buy WPC) before the inbound finalizes, causing the protocol's `depositPRC20WithAutoSwap` to sell the user's PRC20 for fewer WPC than fair value, then reverse the trade afterward, extracting the difference (a classic sandwich/oracle-skew attack against a low-liquidity pool). The same pattern applies to `applyGasRefund`'s swap-back-to-PC leg for the unused-gas refund [6](#0-5) .

This directly parallels the AirPuff finding's root cause — using an unprotected/insufficiently protected on-chain swap price to move user/protocol funds — except here the vulnerability is not "0 minOut" (that specific case has been fixed) but "minOut computed from an easily-skewed spot price with no manipulation-resistant reference (TWAP, external oracle, or governance-configured floor price)."

### Impact Explanation
Each `GAS`/`GAS_AND_PAYLOAD` inbound and each unused-gas refund routes real user/protocol value (PRC20 gas-token deposits) through this swap. An attacker who can consistently skew the pool price ahead of these deterministic, publicly-observable executions can repeatedly extract value up to the 5% band per trade, at the direct expense of users receiving less PC for their gas top-up (and, since this is inside the canonical `PCTx`/`UniversalTx` accounting, the loss is baked into consensus state as "successful" swaps) — this is unauthorized, wrongful diversion of user-controlled/protocol-controlled funds within the universal execution flow (`x/uexecutor/keeper`), constrained to unprivileged trading with no admin/validator collusion required.

### Likelihood Explanation
Moderate to High. The trigger (an inbound reaching quorum) is externally observable ahead of time (any observer can watch `PendingInbounds`/vote counts), and the WPC/PRC20 pools are ordinary public AMM pools with likely limited liquidity for many synthetic tokens, making price manipulation within a single block/few blocks economically feasible for tokens with modest trading volume. No validator or relayer collusion is needed — this is purely an unprivileged trader interacting with a public pool.

### Recommendation
Do not rely solely on an instantaneous `quoteExactInputSingle` spot quote for `minPCOut`. Use a manipulation-resistant reference price — e.g., a time-weighted average price (TWAP) from the pool combined with a bounded deviation check against it, or a governance/oracle-configured reference price for the token — and only fall back to spot-quote-derived slippage bounds when the spot price is within an acceptable deviation of the TWAP/reference. Consider widening protections beyond the flat 5% by also capping the maximum swap size per block relative to pool liquidity, and/or routing gas-token swaps through pools with minimum liquidity guarantees enforced at token-config time in `x/uregistry`.

### Proof of Concept
1. Observe a pending `GAS`/`GAS_AND_PAYLOAD` inbound approaching the 2/3+ UV vote threshold (public ballot state).
2. In the block(s) immediately preceding finalization, submit a large trade against the relevant PRC20/WPC Uniswap V3 pool to depress the PRC20→WPC spot price (feasible given the pool's typically thin seeded liquidity).
3. When the inbound finalizes, `ExecuteInboundGas` fetches `GetSwapQuote` off the now-skewed pool price and computes `minPCOut` from that skewed quote [7](#0-6) ; `CallPRC20DepositAutoSwap` executes at (or near) that unfavorable price.
4. Reverse the manipulating trade afterward to restore the pool price, pocketing the difference between fair value and the manipulated execution price, repeatable for every subsequent inbound touching that pool.

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

**File:** e2e-tests/setup.sh (L4556-4569)
```shellscript

    local pool_token_amount="1"
    local pool_wpc_amount="4"
    if [[ "$token_symbol" == "pSOL" ]]; then
      pool_token_amount="${LOCAL_PSOL_POOL_TOKEN_AMOUNT:-50}"
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
    (
      cd "$SWAP_AMM_DIR"
      node scripts/pool-manager.js create-pool "$token_addr" "$wpc_addr" 4 500 true "$pool_token_amount" "$pool_wpc_amount"
    )
  done < <(jq -r '.tokens[]? | [.symbol, .address] | @tsv' "$DEPLOY_ADDRESSES_FILE")
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
