I have sufficient evidence to complete the analog scan.

### Title
Hardcoded 5% slippage tolerance in Uniswap-quote-based auto-swap paths enables price-manipulation-driven fund loss during gas abstraction and outbound refunds - ([File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go], [File: x/uexecutor/keeper/outbound.go])

### Summary
Every gas-abstraction auto-swap path in `x/uexecutor` (inbound GAS execution, inbound GAS_AND_PAYLOAD execution, and outbound excess-gas refunds) fetches a live on-chain Uniswap V3-style quote and then executes the actual swap with a **hardcoded 5% slippage tolerance** (`minPCOut = quote * 95 / 100`), regardless of trade size or pool depth. Because the pools used (PRC20/WPC) are ordinary, publicly-tradable AMM pools that any unprivileged user can swap against, an attacker can move the pool price ahead of the module's own swap execution and force the protocol-controlled swap to clear up to 5% worse than fair value — extracting value directly from user/protocol funds on every gas-abstraction or refund event, with no admin-tunable or dynamic slippage control.

### Finding Description
`ExecuteInboundGas` fetches a quote via `GetSwapQuote` (calling `QuoterV2.quoteExactInputSingle`, a static read against the live pool) and then computes `minPCOut` with a fixed 5% discount before calling `CallPRC20DepositAutoSwap`: [1](#0-0) 

The same pattern is repeated verbatim in the GAS_AND_PAYLOAD path's `gasAndPayloadDepositAutoSwap`: [2](#0-1) 

and again in the outbound excess-gas refund path's `applyGasRefund`/`getSwapQuoteForRefund`: [3](#0-2) 

The quoted/swapped pools (`PRC20`/`WPC` Uniswap-V3-style pools) are ordinary public AMM pools created and traded via normal, unprivileged EVM transactions — see pool creation via `pool-manager.js create-pool` in the e2e setup, confirming these are standard tradable pools, not privileged/internal-only contracts: [4](#0-3) 

Because the quote-fetch (`GetSwapQuote`, a static `CallEVM`) and the swap-execution (`CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`, a `DerivedEVMCall`) both read live pool state at the moment the module code runs (during processing of the finalizing `MsgVoteInbound`/`MsgVoteOutbound` that reaches quorum), an unprivileged attacker who can predict or observe an imminent quorum-finalizing vote can submit an ordinary swap transaction in the same or preceding block to move the pool price. The module's own swap then executes against this manipulated price, and the fixed 5% tolerance is wide enough to absorb the manipulation without reverting — unlike the external report's DOS concern, here the fixed percentage is *permissive* rather than protective, letting a manipulated trade clear that a variable, liquidity-aware slippage bound would have rejected. The attacker can then reverse their position, capturing the difference between fair value and the manipulated execution price at the expense of the user's inbound funds (for gas-abstraction swaps) or the protocol's refund payout (for outbound refunds).

This directly mirrors the external report's root cause — a fixed slippage percentage applied uniformly regardless of market conditions or pool depth — but manifests here as exploitable value extraction rather than DOS, because the swapped asset (the user's own bridged funds, or protocol refund funds) is externally attacker-influenced via a public, permissionless pool.

### Impact Explanation
Every `GAS` and `GAS_AND_PAYLOAD` inbound, and every outbound with unused gas to refund, routes through one of these three fixed-5%-slippage call sites. An attacker does not need any privileged role — trading on the PRC20/WPC pool is available to any address — to force the protocol's mandatory auto-swap to execute at up to 5% below fair value, directly reducing the native PC amount credited to the user's UEA (gas-abstraction path) or refunded to the recipient (refund path). This is a repeatable, unbounded-frequency drain of user/protocol funds tied to ordinary cross-chain deposit and gas-refund flows — an in-scope "draining ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting [and] refund accounting" impact.

### Likelihood Explanation
Medium-to-High: no privileged access is required, only capital to move a shallow pool and a transaction timed around a quorum-finalizing vote (votes and their proximity to quorum are observable on-chain). Newly-listed or thinly-liquid PRC20/WPC pools are especially susceptible since a fixed 5% tolerance is far more exploitable at low liquidity depths, exactly as the original report describes.

### Recommendation
Replace the hardcoded 5%/95% constant in `execute_inbound_gas.go`, `execute_inbound_gas_and_payload.go`, and `outbound.go` with a slippage bound that is either (a) governance/param-configurable per token or globally via `x/uexecutor` params, and/or (b) derived dynamically from pool liquidity depth/TWAP rather than the instantaneous spot quote, so a single-block price manipulation cannot be absorbed within tolerance. Consider also using a TWAP-based reference quote instead of `quoteExactInputSingle`'s spot price to reduce single-transaction manipulability.

### Proof of Concept
1. Attacker identifies a PRC20/WPC pool with modest liquidity used for gas-abstraction swaps (e.g., a newly onboarded token).
2. Attacker observes an inbound deposit approaching UV quorum (votes are public), predicting the block in which `ExecuteInboundGas`'s auto-swap will fire.
3. Attacker submits a large swap into the pool (sell WPC / buy PRC20, or vice versa) in the block immediately preceding the quorum-finalizing `MsgVoteInbound`, moving the spot price against the module's upcoming trade.
4. `GetSwapQuote` reads the manipulated price, `minPCOut = quote * 95/100` still permits execution at a price up to 5% worse than the pre-manipulation fair value.
5. `CallPRC20DepositAutoSwap` executes the deposit-swap at the manipulated price, crediting the UEA with less PC than fair value.
6. Attacker reverses their position, arbitraging back to par and pocketing the value extracted from the user's deposit (repeatable on every subsequent inbound targeting the same token/pool).

### Citations

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

**File:** x/uexecutor/keeper/outbound.go (L217-223)
```go
	if swapErr == nil {
		quote, quoteErr := k.getSwapQuoteForRefund(ctx, gasToken, fee, refundAmount)
		if quoteErr == nil {
			minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
			minPCOut.Div(minPCOut, big.NewInt(100))

			resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, true, fee, minPCOut)
```

**File:** e2e-tests/setup.sh (L4561-4568)
```shellscript
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
    (
      cd "$SWAP_AMM_DIR"
      node scripts/pool-manager.js create-pool "$token_addr" "$wpc_addr" 4 500 true "$pool_token_amount" "$pool_wpc_amount"
    )
```
