I have enough evidence to identify a valid analog. The pricing pattern in the Sherlock report — deriving a swap/mint valuation from a live, single-source pool price at the moment of execution, with only a fixed slippage buffer computed *from that same manipulable price* — maps directly onto Push Chain's `GetSwapQuote`/auto-swap path used in `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` / `applyGasRefund`.### Title
Auto-swap `minPCOut` slippage bound is derived from the same single-block spot quote it is meant to protect against, enabling spot-price manipulation of the WPC/PRC20 Uniswap V3 pool to extract value during inbound gas-abstraction and gas-refund swaps - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/evm.go)

### Summary
The Sherlock H-1 report shows a valuation methodology bug: an LP's price was derived from a per-block/spot value (`pool.getRate()`) combined with a spot token price, without protection against short-term manipulation, letting a borrower over-borrow against an over-valued position. The equivalent pattern in Push Chain's `x/uexecutor` module is the **auto-swap slippage-protection logic**: `GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` for a spot quote of the PRC20→WPC swap, and the caller then computes `minPCOut = quote * 95 / 100` from that *same* just-fetched spot quote [1](#0-0) . Because the "protection" bound is mathematically derived from the manipulable price itself rather than from an independent/TWAP reference, an attacker who moves the pool's spot price in the same block/tx sequence can force the deposit-triggered swap to execute at an economically incorrect rate while still satisfying its own slippage check.

### Finding Description
Three flows in `x/uexecutor` route external-chain funds through UniversalCore's PRC20-to-WPC auto-swap using this same pattern:

1. `ExecuteInboundGas` (gas-abstraction inbound path): fetches `quote` via `GetSwapQuote`, computes `minPCOut = quote*95/100`, then calls `CallPRC20DepositAutoSwap` which triggers `depositPRC20WithAutoSwap` on the UniversalCore contract [2](#0-1) .
2. `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound path): identical quote→minPCOut→swap sequence [3](#0-2) .
3. `applyGasRefund` (outbound gas-refund path): same pattern used to swap the excess gas token back to PC before refunding a user [4](#0-3) .

`GetSwapQuote` performs a static (`commit=false`) call to `QuoterV2.quoteExactInputSingle`, i.e. it reads the **current spot price** of the on-chain WPC/PRC20 Uniswap V3 pool at execution time, with no averaging, no oracle cross-check, and no minimum liquidity/depth requirement [5](#0-4) . The resulting `minPCOut` bound is then computed as a fixed 5% haircut of that very same spot quote — it does not reference any external/independent price feed (unlike the ChainMeta gas-price oracle, which is committee-voted and median-aggregated [6](#0-5) ).

Any unprivileged user can submit a large swap against the same Uniswap V3 WPC/PRC20 pool that UniversalCore uses (this pool is a standard, permissionlessly tradable pool since it's deployed for the token pair), skewing the spot price immediately before an inbound deposit or an outbound gas refund executes. Because `minPCOut` is derived from the same skewed price, it offers no real protection — the executed swap proceeds at the manipulated rate, and the attacker can:
- swap the pool back afterward (classic sandwich), extracting the price impact as profit while the protocol's auto-swap absorbs the loss, or
- push the price in a direction that causes the protocol to mint/pay out more native `upc` than the actual value of the PRC20 deposited, i.e. minting value not backed by the bridged asset.

This mirrors the Sherlock issue's core defect exactly: a value used to authorize a fund-moving operation is computed from an attacker-influenceable, single-block market price with no external cross-check, and the "protection" (min-out here, min-price-vs-getRate there) is derived from that same tainted input.

### Impact Explanation
Every inbound `GAS` and `GAS_AND_PAYLOAD` deposit, and every outbound gas-fee refund with excess gas, routes PRC20 tokens through this same spot-priced auto-swap into native `upc`. An attacker can manipulate the pool price around the time these swaps execute to force the protocol to give out more `upc` than the PRC20 amount is actually worth, or to make honest users' deposits land with far less PC than expected. Repeated over many inbound/outbound cycles, this allows systematic value extraction from the protocol's WPC reserves — a direct analog to "protocol insolvency from overvaluation," matching the in-scope impact of "corruption of PRC20 or native asset accounting" and "unauthorized state transitions in universal execution flows."

### Likelihood Explanation
The trigger requires only: (a) an unprivileged attacker capable of submitting ordinary EVM swap transactions against the WPC/PRC20 pool, and (b) a normal user (or the attacker's own) inbound/outbound event reaching the auto-swap execution point — both are unprivileged, default transaction paths reachable by any external actor, requiring no validator, relayer, or admin collusion. The main uncertainty (since Solidity contract source for `UniversalCore.sol`/the pool depth, liquidity locking, or any additional guard such as a maximum price-impact check was not found in the indexed Go/ABI code) is whether the pool has enough liquidity depth or additional contract-side protections (e.g., TWAP-based caps) that are not visible from the Go-side keeper code alone; that would need verification directly in the Solidity contracts, which were not present in the indexed repository content.

### Recommendation
Replace the spot-quote-derived slippage bound with a price reference that an attacker cannot move in the same transaction/block as the swap execution: use the already-existing ChainMeta-style committee-median gas price/oracle mechanism, or a TWAP over multiple blocks from the Uniswap V3 pool (`observe()`), to compute `minPCOut` independently of the just-fetched spot quote. Additionally consider bounding the maximum allowed deviation between the spot quote and the TWAP, and/or capping per-tx swap size relative to pool liquidity, before allowing `depositPRC20WithAutoSwap` / `refundUnusedGas` (`withSwap=true`) to proceed.

### Proof of Concept
1. Attacker identifies the UniversalCore-deployed Uniswap V3 pool for `PRC20 <-> WPC` used for a specific token (queryable via `GetUniversalCoreQuoterAddress`/`GetDefaultFeeTierForToken`) [7](#0-6) .
2. Attacker submits a large swap into the pool that heavily moves the PRC20→WPC spot price in their favor just before (or interleaved with) processing of a pending `GAS`/`GAS_AND_PAYLOAD` inbound or an outbound gas refund with excess gas.
3. When validators execute the inbound (`ExecuteInboundGas`) or the outbound refund (`applyGasRefund`), `GetSwapQuote` reads the now-skewed spot price, and `minPCOut = quote*95/100` is computed from that skewed price [1](#0-0) [4](#0-3) .
4. `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` executes the swap against the manipulated pool, minting/paying `upc` at the skewed rate.
5. Attacker reverses their initial swap, realizing the price-impact profit while the protocol/user absorbs the mispriced exchange.

Full confirmation of exploitability (pool liquidity depth, existence of any contract-level TWAP guard in `UniversalCore.sol`) requires reading the Solidity contract source, which is not present in the indexed portion of this repository — a Devin session with full repo/file access would be needed to verify the exact guard rails (if any) inside `UniversalCore.sol`'s `depositPRC20WithAutoSwap`/`refundUnusedGas` implementations.

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

**File:** x/uexecutor/keeper/chain_meta.go (L156-166)
```go
	// Compute independent upper medians (len/2) for price and chain height.
	medianPrice := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.price })
	medianChainHeight := upperMedianUint64(fresh, func(v voteSnapshot) uint64 { return v.chainHeight })

	k.Logger().Debug("chain meta medians computed",
		"chain_id", observedChainId,
		"fresh_votes", len(fresh),
		"median_price", medianPrice,
		"median_chain_height", medianChainHeight,
	)

```
