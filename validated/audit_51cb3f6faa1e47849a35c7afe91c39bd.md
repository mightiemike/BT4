## Analysis

I traced the external Stork/Pyth "pull-oracle arbitrage" bug class to Push Chain's `x/uexecutor` module. The `ChainMeta` gas-price oracle (`VoteChainMeta` / `MsgVoteChainMeta`) is the closest structural analog to a pull oracle, but it requires the caller to be a bonded, non-tombstoned Universal Validator (`msg_server.go` calls `IsBondedUniversalValidator` before accepting a vote), which puts it in the excluded "malicious validator" category [1](#0-0) .

However, the same underlying bug class — **a manipulable, pull-style spot price consumed atomically by protocol logic with only a fixed percentage slippage bound and no staleness/TWAP protection** — exists in the Uniswap V3 `QuoterV2` spot-quote path used for auto-swap deposits and gas refunds, and this path is reachable by an ordinary unprivileged user.

`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle`, which simulates a swap against the *current* pool reserves (a spot price, fully manipulable within the same or an adjacent block, exactly like the pull-model Stork/Pyth price the report describes): [2](#0-1) 

This quote is used to compute `minPCOut` with a flat 5% slippage tolerance and immediately consumed by `CallPRC20DepositAutoSwap` for `GAS`/`GAS_AND_PAYLOAD` inbounds: [3](#0-2) 

The identical pattern (quote → 5% slippage → swap) is reused for the gas-fee refund on outbound completion: [4](#0-3) 

### Title
Manipulable spot AMM price consumed for PRC20 auto-swap and gas-refund swaps with only fixed 5% slippage, no TWAP/staleness protection - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Push Chain's `x/uexecutor` module fetches a live Uniswap V3 `QuoterV2` spot quote (`GetSwapQuote`) and immediately executes a real swap (`CallPRC20DepositAutoSwap` / `refundUnusedGas` with `withSwap=true`) bounded only by a flat 5% slippage tolerance derived from that same spot quote. Because the quote reflects the pool's instantaneous reserves rather than a time-weighted or otherwise manipulation-resistant price, an unprivileged attacker who controls the relevant liquidity pool can move the spot price immediately before the protocol-triggered swap executes, extracting value from the protocol/user funds involved in the deposit or refund — the same "pull-price, trade, close position" pattern described in the referenced Stork/Pyth report, applied to Push Chain's internal AMM-based price source instead of an external oracle.

### Finding Description
For `GAS` and `GAS_AND_PAYLOAD` inbound routes, once a Universal Validator quorum finalizes an inbound, `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` compute a swap quote via `GetSwapQuote` and pass `minPCOut = quote * 95 / 100` to `CallPRC20DepositAutoSwap`, which performs the real PRC20→WPC swap on-chain [5](#0-4) . `GetSwapQuote` itself is a direct call into `QuoterV2.quoteExactInputSingle`, which computes output against the pool's current reserves at call time — a spot price with no time-weighting or staleness window [6](#0-5) .

The same quote-then-swap pattern with the same fixed 5% band is used again for the excess-gas refund path on outbound completion (`applyGasRefund` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas` with `withSwap=true`) [4](#0-3) .

Because the underlying Uniswap V3 pool (WPC/PRC20 pair) is an ordinary, permissionless AMM, any unprivileged user can trade against it to move its spot price. An attacker can:
1. Move the pool's spot price against the protocol's favor (buy up WPC to make PRC20→WPC output crater, or vice versa depending on desired direction).
2. Trigger (or wait for) an inbound deposit/outbound-refund that they control (their own bridge transaction/GAS route inbound, or their own outbound observation) to be finalized by honest validators while the price is still distorted.
3. Let the protocol's `depositPRC20WithAutoSwap` / `refundUnusedGas` execute at the distorted spot price, bounded only by the 95% floor derived from that same distorted quote (so the distortion itself sets the floor — it offers no protection against manipulation, only against slippage from ordinary trading during the swap itself).
4. Reverse their price-moving trade afterward to realize the arbitrage/extraction, exactly mirroring the "trade → set/consume price → close position" sequence from the Stork/Pyth report.

This differs from the excluded scenario (malicious validators/oracle dishonesty) because the manipulation vector is the attacker's own permissionless AMM trades, not a corrupted validator vote or oracle feed; the validators are honestly reporting real inbound/outbound events, but the *swap price the protocol consumes* is attacker-controllable.

### Impact Explanation
An unprivileged attacker who owns or influences liquidity in the relevant Uniswap V3 pool can extract value from protocol-triggered swaps (auto-swap deposits and gas-fee-refund swaps), effectively draining PRC20/WPC value from the deposit or refund flow at the expense of the protocol/other users. The magnitude scales with pool depth/liquidity and volatility, similar to the "especially in moments of high volatility" caveat in the original report. This maps to the in-scope "corruption of PRC20 or native asset accounting ... refund accounting" and "draining ... of user or protocol-controlled funds" impact buckets.

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to have meaningful capital/liquidity relative to the pool and to time their own bridge transaction (or outbound observation) to land near their price manipulation, but no privileged access, no validator collusion, and no oracle compromise are needed — only ordinary permissionless AMM trading plus normal use of the bridge, matching the "unprivileged trigger" requirement.

### Recommendation
Do not use an instantaneous `QuoterV2.quoteExactInputSingle` spot quote as the sole basis for `minPCOut`. Use a manipulation-resistant reference price (e.g., a TWAP over a meaningful window, or cross-check against the `ChainMeta`/external price oracle) for computing slippage bounds, and/or cap the deviation allowed between the spot quote and a longer-window reference price before proceeding with `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. Consider falling back to the no-swap deposit/refund path whenever the spot-vs-reference deviation exceeds a safe threshold, similar to how the existing code already has a no-swap fallback for other failure modes.

### Proof of Concept
1. Attacker provides most of the liquidity (or otherwise has capital advantage) in the WPC/PRC20 Uniswap V3 pool used by `UniversalCore`.
2. Attacker submits a large trade shifting the pool's instantaneous price so that `quoteExactInputSingle` for PRC20→WPC returns an output far below the pool's "fair"/recent-average price.
3. Attacker (or an accomplice) triggers a `GAS`/`GAS_AND_PAYLOAD` inbound deposit (bridging PRC20 in) that Universal Validators finalize while the price is still distorted; `ExecuteInboundGas` computes `minPCOut` from the distorted quote and executes `depositPRC20WithAutoSwap`, which swaps at the distorted price [7](#0-6) .
4. Attacker reverses their price-moving trade, restoring the pool to fair price and capturing the arbitrage difference, having caused the protocol's swap to execute at an unfavorable rate.

### Citations

**File:** x/uexecutor/keeper/msg_server.go (L147-170)
```go
// VoteChainMeta implements types.MsgServer.
func (ms msgServer) VoteChainMeta(ctx context.Context, msg *types.MsgVoteChainMeta) (*types.MsgVoteChainMetaResponse, error) {
	signerAccAddr, err := sdk.AccAddressFromBech32(msg.Signer)
	if err != nil {
		return nil, fmt.Errorf("invalid signer address: %w", err)
	}

	signerValAddr := sdk.ValAddress(signerAccAddr)

	isBonded, err := ms.k.uvalidatorKeeper.IsBondedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check bonded status for signer %s", msg.Signer)
	}
	if !isBonded {
		return nil, fmt.Errorf("universal validator for signer %s is not bonded", msg.Signer)
	}

	isTombstoned, err := ms.k.uvalidatorKeeper.IsTombstonedUniversalValidator(ctx, msg.Signer)
	if err != nil {
		return nil, errors.Wrapf(err, "failed to check tombstoned status for signer %s", msg.Signer)
	}
	if isTombstoned {
		return nil, fmt.Errorf("universal validator for signer %s is tombstoned", msg.Signer)
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
