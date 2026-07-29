I have enough evidence now to write the analog finding. This is a direct, well-documented match to the formPOL bug class.

### Title
Same-block spot-price quote used as slippage floor makes `depositPRC20WithAutoSwap` / `refundUnusedGas` swaps sandwichable - (File: x/uexecutor/keeper/evm.go)

### Summary
The formPOL report's root cause — computing a slippage bound from a price source that the attacker can move in the same transaction context, rather than a manipulation-resistant feed (TWAP/Chainlink) — has a direct analog in Push Chain's inbound gas/funds auto-swap path. `GetSwapQuote` fetches `QuoterV2.quoteExactInputSingle` (an instantaneous, current-tick simulation over the live Uniswap V3 pool) and the resulting `minPCOut = quote * 95%` is passed straight into the same-block `depositPRC20WithAutoSwap` / `refundUnusedGas` call.

### Finding Description
`k.GetSwapQuote` ( [1](#0-0) ) performs a static `quoteExactInputSingle` call against the pool's *current* reserves/tick with `sqrtPriceLimitX96=0`, i.e. an instantaneous spot-price simulation, not a TWAP. Immediately afterward, `ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap` derive `minPCOut = quote * 95 / 100` and pass it into `CallPRC20DepositAutoSwap`, which fires `depositPRC20WithAutoSwap` on `UniversalCore` in the very next EVM call within the same keeper execution (same block): [2](#0-1) .

Because the "floor" is derived from a price an attacker can move an instant beforehand (any ordinary EVM user can trade against the same UniversalCore/Uniswap-V3 WPC pool via a normal transaction ordered ahead of the inbound-execution tx in the same block), the 5% band only protects against the swap's own price impact — not against pool-ratio manipulation. An attacker can:
1. Front-run: swap into the pool to push the PRC20/WPC price so that a *depressed* PC output looks acceptable under the pool's own (now-skewed) 95% floor.
2. Let the victim's `depositPRC20WithAutoSwap` (executed via `MsgVoteInbound` threshold finalization) swap PRC20→WPC at the skewed ratio — worse execution than the true pre-attack price, but still above the manipulated 95% floor since the floor was computed *after* the manipulation.
3. Back-run: reverse the initial swap to restore the pool ratio and capture the value extracted from the protocol's auto-swap.

The identical pattern is reused for excess-gas refunds in `applyGasRefund` → `getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas`, so both the inbound top-up leg and the outbound refund leg are exposed: [3](#0-2) .

Additionally, the deposit call is issued with `deadline = 0 → contract uses its default` rather than a caller-bound near-term deadline ( [4](#0-3) ), so there is no independent freshness guard beyond the (manipulable) slippage bound.

This mirrors the audited formPOL issue exactly: the mitigation there was "should have slippage protection based off a maximum deviation from Uniswap v3 TWAP or Chainlink" — here the 5% band exists, but is computed from the same manipulable AMM instant price rather than an external/TWAP anchor, so it provides no real protection against sandwiching.

### Impact Explanation
Every `GAS` and `GAS_AND_PAYLOAD` inbound (and every outbound excess-gas refund) routes bridged value through this auto-swap. An attacker who can move the WPC/PRC20 pool price before the protocol's swap executes captures value that should have gone to the bridging user or stayed in protocol-controlled reserves — a partial, repeatable drain of user/protocol funds through legitimate inbound/refund flows, without any privileged access. This falls under "corruption of PRC20 or native asset accounting … reachable from ordinary user deposits … alone," per the allowed-impact gate.

### Likelihood Explanation
Requires only unprivileged EVM transactions against the public Uniswap V3 pool wired into `UniversalCore` (deployed at a fixed system-contract address) and normal transaction-ordering/MEV capability — no validator, TSS, or admin privilege needed. The attack is triggered purely by the existence of pending, quantifiable inbound gas/funds transactions (visible once a `MsgVoteInbound` reaches threshold) or outbound refunds, both of which are externally observable.

### Recommendation
Anchor the slippage floor to a manipulation-resistant reference (e.g., a Uniswap V3 TWAP observation over a window that cannot be moved within a single block/transaction, or an external oracle) instead of `quoteExactInputSingle`'s instantaneous quote, and compare the two so any excessive deviation reverts the swap and falls back to the no-swap PRC20 deposit path. Bind a tight, real deadline (e.g., current block time + few seconds) rather than `deadline = 0`.

### Proof of Concept
1. Attacker observes a large pending `GAS_AND_PAYLOAD` inbound about to reach the 2/3 UV vote threshold (public mempool/chain state).
2. Attacker submits a normal EVM swap against the same WPC/PRC20 Uniswap V3 pool used by `UniversalCore`, moving the pool price so PRC20 is temporarily overpriced relative to WPC, ordered before the finalizing `MsgVoteInbound` tx in the block.
3. `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` calls `GetSwapQuote` against the now-skewed pool, computes `minPCOut = quote*95%` from the skewed price, and `CallPRC20DepositAutoSwap` executes the victim's swap at the skewed ratio (worse WPC output than pre-attack conditions, but still satisfying the skewed floor).
4. Attacker submits a back-run swap in the same block reversing step 2, netting the price difference extracted from the protocol's swap as profit.

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

**File:** x/uexecutor/keeper/evm.go (L574-592)
```go
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress, // who is sending the transaction
		handlerAddr,        // destination: Handler contract
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20WithAutoSwap",
		prc20Address,
		amount,
		to,
		fee,
		minPCOut,
		big.NewInt(0), // deadline = 0 → contract uses its default
	)
```

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
