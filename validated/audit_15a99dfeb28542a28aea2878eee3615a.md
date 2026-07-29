## Analysis

The Arrakis bug class is: an operator can call a value-moving function (`rebalance`) repeatedly, and even though each individual call respects a slippage-tolerance check, the check is measured *relative to the manipulable value itself* (a swap's own execution price), so repeated calls compound losses far beyond what a single-call tolerance was meant to bound, and there is no rate limit or cumulative-loss cap.

The Push Chain analog is the gas-abstraction auto-swap path used on inbound deposits (`GAS` / `GAS_AND_PAYLOAD`) and on outbound gas-fee refunds, in `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, and `x/uexecutor/keeper/outbound.go` (`applyGasRefund`). Each of these paths calls `k.GetSwapQuote` [1](#0-0)  which queries `QuoterV2.quoteExactInputSingle` for the pool's **current live spot price**, and then derives `minPCOut` purely as 95% of that same quote: [2](#0-1) 

This "slippage protection" is self-referential — it bounds only the deviation *within a single swap call*, but does not check the quote against any independent reference price (no Chainlink-style oracle, no TWAP, no `ChainMeta`-based sanity check). Any inbound deposit (fully unprivileged: an attacker just sends a deposit on an external chain, which honest Universal Validators vote in through normal `MsgVoteInbound` quorum, no validator collusion required) triggers this pattern: [3](#0-2) 

Because:
1. The reference price is the pool's own spot price at execution time (attacker-manipulable via ordinary swaps against the same PRC20/WPC pool),
2. There is no limit on how many separate inbound deposits an attacker can submit and have processed, and
3. There is no cumulative-loss cap analogous to what the Arrakis report recommended ("evaluate `totalUnderlyingWithFees` before and after execution"),

an attacker can repeatedly move the PRC20/WPC pool price and then push a stream of inbound deposits through `CallPRC20DepositAutoSwap`, each one individually "passing" the 5% slippage check against the corrupted spot price, but collectively draining pool liquidity — precisely the "accept the tolerance N times to drain far more than N× the tolerance" pattern from the seed report. The same self-referential quote/refund pattern is repeated in the outbound gas refund path (`applyGasRefund`), which is also triggered on ordinary user-visible outbound observations with no per-tx or cumulative cap: [4](#0-3) 

### Title
Repeated unprivileged inbound/outbound auto-swaps use self-referential spot-price "slippage" checks with no rate or cumulative-loss limit, allowing drain of the PRC20/WPC swap pool - (File: x/uexecutor/keeper/evm.go, execute_inbound_gas.go, execute_inbound_gas_and_payload.go, outbound.go)

### Summary
`GetSwapQuote` fetches the live spot price directly from the on-chain `QuoterV2` pool used for PRC20↔WPC auto-swaps, and `minPCOut` is computed as a flat 95% of that same live quote in every gas-abstraction deposit path (`ExecuteInboundGas`, `ExecuteInboundGasAndPayload`) and in the outbound excess-gas refund path (`applyGasRefund`). There is no external reference price, no TWAP, and no limit on the number of inbound deposits or refunds an unprivileged attacker can trigger. This mirrors the Arrakis `SimpleManager.rebalance` finding: a per-call tolerance check that is technically respected on every call, but is measured against a value the attacker controls, and can be invoked without limit to accumulate loss far beyond the intended single-call tolerance.

### Finding Description
`GetSwapQuote` at [1](#0-0)  reads the pool's current spot price via `quoteExactInputSingle`. Every caller (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, `getSwapQuoteForRefund`) derives `minPCOut` as `quote * 95 / 100` and immediately performs the swap in the same call via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. Because the "quote" and the "minimum acceptable output" are both sourced from the pool's instantaneous, attacker-influenceable state, the 5% band bounds only per-call execution slippage — it provides no protection against the pool price itself having been pushed to a bad level beforehand. An attacker can:
1. Manipulate the PRC20/WPC pool price with ordinary swaps.
2. Submit any number of small deposits on a supported external chain (`GAS` or `GAS_AND_PAYLOAD` inbound type), each independently observed and voted in by honest validators.
3. Have each corresponding `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` call auto-swap the deposit against the manipulated pool, each individually satisfying its own 5% band, but collectively extracting far more value than any single-call tolerance was designed to allow, since there is no per-window or per-address rate limit and no cumulative pre/post value check across calls.

### Impact Explanation
Repeated unprivileged deposits can drain the liquidity of the PRC20/WPC swap pool used for gas abstraction, and the same self-referential pattern affects excess-gas refunds paid out of `UniversalCore`, resulting in loss of protocol/pool funds attributable purely to the absence of rate limiting or a trustworthy price reference, without requiring any compromised validator, operator, or governance actor.

### Likelihood Explanation
No privileged role is required — any user who can submit deposits on a connected external chain and trade against the same pool on Push Chain can execute this pattern purely through the default inbound-processing pipeline. The only variable affecting attacker profit is pool depth/fee tier, exactly as discussed for the Arrakis analog.

### Recommendation
Anchor `minPCOut` / slippage checks to an external reference (e.g., a TWAP over multiple blocks, or a price oracle independent of the same pool being traded), and/or enforce a rate limit or cumulative-loss cap on auto-swap volume executed per block/window/token, evaluating pool or protocol reserves before and after a bounded set of swaps rather than per-call in isolation.

### Proof of Concept
1. Attacker trades against the PRC20/WPC pool used by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress` to push its spot price away from fair value.
2. Attacker sends N small deposits of the same PRC20-backed asset on the source chain, tagged `GAS`/`GAS_AND_PAYLOAD`.
3. Honest validators vote each inbound in via normal quorum; `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` fires for each, calling `GetSwapQuote` against the still-manipulated pool and computing `minPCOut = quote*95/100` [5](#0-4) .
4. Each of the N deposits is swapped at the bad price with the check trivially satisfied, since the check is defined relative to the corrupted price itself — repeat until the pool/protocol reserve is drained.

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
