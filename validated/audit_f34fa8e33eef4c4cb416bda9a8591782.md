## Analysis Result

Modeling the Boost bug-class ("a protective price/peg check that is computed from the very state being manipulated in the same operation, and thus fails to guarantee the intended fair-price outcome") onto Push Chain's scope, the closest reachable analog is in the module-originated Uniswap V3 auto-swap path used for inbound `GAS`/`GAS_AND_PAYLOAD` deposits and for outbound gas-fee refunds.

### Title
Module-originated PRC20→WPC auto-swaps use a same-block, self-referential QuoterV2 spot quote as slippage floor, allowing sandwich extraction of user/protocol funds - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Whenever `uexecutor` needs to convert a PRC20 gas token into WPC (native PC) on behalf of a user — during `ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund`'s outbound gas-fee refund — it fetches a quote from the Uniswap V3 `QuoterV2` contract and derives `minPCOut` as a flat 95% of that quote, then immediately executes the swap in the same message-processing call. Because the "protective" floor is computed from the live, manipulable AMM pool state right before the swap executes, an unprivileged attacker who can influence pool price ordering within the same block (a standard sandwich pattern) can move the pool price so the quote itself is depressed, and the module's swap will still satisfy its own (attacker-degraded) `minPCOut`, extracting value from the deposit/refund amount. This mirrors the Boost report's root cause: a check intended to enforce a fair/peg price is derived from the same transient state it is supposed to protect against, so it doesn't actually bound the executed price to a fair reference.

### Finding Description
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `commit=false` to get `amountOut` for the exact `prc20 -> WPC` trade about to be executed: [1](#0-0) 

That quote is then used directly to compute `minPCOut` with a hardcoded 5% tolerance and passed to the real swap call (`commit=true`) in the very same keeper invocation, for inbound gas auto-swap: [2](#0-1) 

and again for `GAS_AND_PAYLOAD`: [3](#0-2) 

and again for the outbound excess-gas refund swap: [4](#0-3) [5](#0-4) 

There is no TWAP, no independent oracle reference, and no check that the pool state at quote-time reflects a fair/expected price — the quote and the swap both read the identical, single mutable AMM pool (`UniswapV3Quoter`/`WPC` pair configured on `UniversalCore`) inside the same finalization flow triggered by ordinary user actions (an inbound GAS deposit reaching validator quorum, or an outbound vote reaching quorum for the refund path). Any party able to place a swap against that pool immediately before the validator-triggered `DerivedEVMCall` lands (e.g., ahead of it in the same block via normal mempool positioning) can move the spot price down, causing `GetSwapQuote` to return a degraded `amountOut`, and the depressed `minPCOut` (`quote * 95%`) still gets satisfied by the manipulated swap — because the "floor" itself was derived from the manipulated pool. This is structurally identical to the Boost report's flaw: `minUsdAmountOut < toUsdAmount(boostAmount)` looks like a safety check but is computed from state that doesn't actually bound the trade to the intended (fair/peg) price.

### Impact Explanation
This affects real user/protocol funds: the PRC20 deposited by an inbound `GAS`/`GAS_AND_PAYLOAD` transaction, and the excess gas-fee refund owed to a sender/fund-recipient on outbound completion, are both converted through this vulnerable auto-swap path. An attacker who sandwiches the swap can capture the price-impact difference at the expense of the depositor/protocol, i.e. the recipient UEA or refund recipient receives less WPC than the fair value of the swapped PRC20, while the attacker's own trade profits from the temporary price distortion — a direct, unauthorized value extraction from user/protocol-controlled funds reachable purely through ordinary inbound/outbound flows with no privileged access required.

### Likelihood Explanation
Every `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound with an unused-gas refund goes through this code path automatically once validator quorum is reached, so the trigger condition (a pending auto-swap) is common and predictable from observing chain state/mempool. Executing a sandwich only requires the ability to place a trade against the same Uniswap V3 pool before the module's swap executes in block ordering — no validator, TSS, or admin privilege is needed, matching the "unprivileged external attacker" threat model.

### Recommendation
Do not derive the slippage floor from a quote taken from the same manipulable pool state immediately preceding execution. Use a TWAP-based reference price (or an external attested price) independent of the single-block spot state, and/or cap per-swap price impact against a governance-configured reference price rather than a self-referential same-call quote.

### Proof of Concept
1. Attacker monitors pending inbound votes/outbound observations that will trigger `ExecuteInboundGas` / `applyGasRefund` auto-swap for a given PRC20 gas token against the `WPC` pool.
2. Attacker submits a large swap into the same Uniswap V3 pool (via a standard EVM tx) positioned immediately before the validator-quorum-triggering transaction in the block, depressing the `prc20 -> WPC` price.
3. `GetSwapQuote` (called with `commit=false` right before the real swap) reflects the manipulated price; `minPCOut = quote * 95 / 100` is computed from this degraded value. [6](#0-5) 
4. `CallPRC20DepositAutoSwap`/`refundUnusedGas` executes the real swap, which satisfies the degraded `minPCOut` but yields far less WPC than the pre-manipulation fair price.
5. Attacker back-runs to restore the pool price and pockets the extracted value; the recipient UEA / refund recipient receives less WPC than the fair value of the converted PRC20.

### Citations

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

**File:** x/uexecutor/keeper/outbound.go (L259-270)
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
}
```
