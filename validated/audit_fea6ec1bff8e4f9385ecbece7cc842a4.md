Based on extensive research, I did not find a literal analog to "simple vs. compound interest" (i.e., a discrete-sampling approximation of a continuously-compounding rate). However, tracing the same underlying bug *class* — **a rate/quote is sampled at one point and then reused (or used to bound itself) in a way that lets a fast-growing discrepancy be exploited, with the size of the error scaling with transaction/trade volume** — leads to a genuine, concretely reachable issue in the PRC20 auto-swap and gas-refund paths of `x/uexecutor`.

### Title
Self-referential Uniswap V3 spot quote used as its own slippage bound in PRC20 auto-swap / gas-refund flows enables sandwich extraction of bridged funds - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
`GAS` / `GAS_AND_PAYLOAD` inbound execution and the successful-outbound gas-fee-refund path both swap a PRC20 token to native PC through `UniversalCore.depositPRC20WithAutoSwap` / `refundUnusedGas`. The `minPCOut` slippage floor passed to that swap is derived from `GetSwapQuote`, a same-block spot quote from the Uniswap V3 `QuoterV2` contract, with a flat 5% tolerance [1](#0-0) . Because the quote used to validate the swap is fetched from the same manipulable AMM pool the swap itself will execute against, an unprivileged attacker can move the pool price before their own inbound is processed, causing the module to accept an artificially unfavorable execution price while still satisfying its own (attacker-influenced) `minPCOut` check.

### Finding Description
`CallPRC20DepositAutoSwap` and `CallUniversalCoreRefundUnusedGas` both take a pre-computed `minPCOut` parameter that the keeper is responsible for deriving before issuing the derived EVM call [2](#0-1) [3](#0-2) . That value is computed as:

```
quote  := GetSwapQuote(quoterAddr, gasToken, wpc, fee, amount)   // Uniswap V3 QuoterV2, spot
minPCOut := quote * 95 / 100
```

This pattern repeats in three places: the `GAS` inbound route [4](#0-3) , the `GAS_AND_PAYLOAD` inbound route [5](#0-4) , and the excess-gas refund on a successfully observed outbound [6](#0-5) .

`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `commit=false` against current pool reserves — a plain spot price, not a TWAP or any external reference [7](#0-6) . Because the *same* spot price is used both to size the trade's floor (`minPCOut`) and to execute the trade, the check is tautological with respect to price manipulation: the check can never fail from manipulation, only from unrelated pool-state changes. The `amount` swapped is the user/attacker-controlled bridged amount from their own inbound (`inbound.Amount`), and its execution timing is knowable to the party that submitted the inbound (they can watch for `VoteInbound`/`VoteOutbound` quorum being reached). An attacker can:
1. Submit (or already have pending) an inbound/outbound that will trigger an auto-swap or gas refund of a PRC20 they know the address of.
2. Move the low-liquidity `gasToken/WPC` Uniswap V3 pool's spot price shortly before the module's `DerivedEVMCall` executes (a normal swap on the pool, no admin/validator privilege required).
3. Let the module fetch its "protective" quote from the now-manipulated pool and pass 95% of that manipulated number as `minPCOut`.
4. The module's swap executes at the manipulated price, converting bridged/refunded PRC20 into native PC at a rate favorable to the manipulated pool state.
5. Attacker reverses their manipulating trade, capturing the price impact as profit — a classic sandwich, except the "slippage protection" was derived from the very price being sandwiched, so it provides no actual defense.

The size of the extractable discrepancy scales with the swap `amount` (bridged volume) and pool illiquidity — directly mirroring the referenced report's point that "the size of the discrepancy... will depend on the volume of transactions."

### Impact Explanation
This corrupts PRC20/native asset accounting on user deposits and refunds — the exact "Registry and accounting path" invariant the scope calls out (token mapping / gas token semantics must not misroute value). Concretely, bridged users receive less native PC than fair value on `GAS`/`GAS_AND_PAYLOAD` inbounds, and gas-refund recipients receive less PC than the owed excess-gas refund, with the difference extracted by whoever manipulates the pool — reachable by any unprivileged party who can trade on the relevant Uniswap V3 pool and knows (or can infer) when their own inbound/outbound will be processed. This is a fund-drain from bridged/protocol value, not merely a display or estimate issue.

### Likelihood Explanation
Reachable via ordinary, honest-validator-processed inbound/outbound flows — no relayer or validator misbehavior needed. Likelihood is highest for gas tokens with thin on-chain Uniswap V3 liquidity (a plausible state for newly-listed or low-volume gas tokens); the 5% tolerance further limits (but does not eliminate) the attacker's边 margin, and gas cost of the manipulating trades must be smaller than the extracted value, which is realistic for larger bridged amounts.

### Recommendation
Do not derive the slippage floor from the same spot price the swap will execute against. Use a manipulation-resistant reference (TWAP over a meaningful window, or an external/registry-configured price) to compute `minPCOut`, or bound the acceptable price deviation between an independent oracle price and the swap's realized price, rejecting/falling back to the no-swap path when they diverge beyond a safe threshold.

### Proof of Concept
1. Identify a `gasToken` PRC20 with low liquidity in its `WPC` Uniswap V3 pool used by `GetDefaultFeeTierForToken`/`GetSwapQuote`.
2. Submit a `GAS_AND_PAYLOAD` (or `GAS`) inbound bridging a large amount of that `gasToken`.
3. Just before validators finalize/execute the inbound (or before the outbound-refund vote finalizes), submit a large swap on the same pool that depresses the `gasToken`→`WPC` spot price.
4. Observe that `GetSwapQuote` returns the depressed quote, `minPCOut = quote*95/100` is set accordingly, and `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes at the manipulated price — user/recipient receives less PC than fair value.
5. Reverse the manipulating trade to realize the price-impact profit, confirming the "slippage protection" never rejected the manipulated execution.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L126-148)
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

**File:** x/uexecutor/keeper/evm.go (L540-546)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
```

**File:** x/uexecutor/keeper/evm.go (L595-605)
```go
// CallUniversalCoreRefundUnusedGas calls refundUnusedGas on UniversalCore to return excess gas fee
// to the recipient. withSwap=true swaps the gas token back to PC; withSwap=false deposits PRC20 directly.
func (k Keeper) CallUniversalCoreRefundUnusedGas(
	ctx sdk.Context,
	gasToken common.Address,
	amount *big.Int,
	recipient common.Address,
	withSwap bool,
	fee *big.Int,
	minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L347-378)
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
