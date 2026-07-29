## Analysis

The external report's core bug class is: **an unprivileged actor can distort an AMM's price/reserve ratio, and a downstream mint/conversion calculation that trusts that same distorted state (with no minimum-liquidity floor or independent price check) then mis-prices value for everyone else.**

The closest native analog in Push Chain is the gas-abstraction auto-swap path: `GAS` / `GAS_AND_PAYLOAD` inbound execution and outbound gas-fee refunds both convert PRC20 → PC by calling a live Uniswap V3 `QuoterV2` for a spot quote, then applying a flat percentage slippage floor derived from that *same* instantaneous quote, then immediately executing the real swap against the same pool. [1](#0-0) 

```
GetSwapQuote → quoterABI.quoteExactInputSingle (commit=false, live pool state)
```
then in the gas execution flow: [2](#0-1) 

```go
quote, execErr = k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
...
minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
minPCOut.Div(minPCOut, big.NewInt(100))
receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
```

The identical pattern repeats for `GAS_AND_PAYLOAD` deposits and for outbound excess-gas refunds: [3](#0-2) [4](#0-3) 

`minPCOut` is a 5% slippage floor computed from the **same block-state quote** used for the swap itself — there is no TWAP, no independent oracle, and no minimum-liquidity gate on the referenced Uniswap V3 pool. This is structurally the same failure mode as the report: a value-conversion calculation trusts a manipulable, self-referential pool state instead of enforcing an independent floor, so anyone able to move the pool's spot price within the same execution window (ordinary swap transactions on the public pool, no validator/TSS/admin privilege required) can extract value from every protocol-driven PRC20→PC conversion routed through this path — most severely against thinly-liquidated, newly onboarded PRC20 tokens (mirroring the "artificially skewed reserve ratio" scenario in the original report).

### Title
Slippage floor for module-driven PRC20→PC auto-swaps is derived from the same manipulable instantaneous AMM quote it protects against - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`GetSwapQuote` reads a live `QuoterV2.quoteExactInputSingle` spot quote from the registered Uniswap V3 pool, and `minPCOut` (the only slippage protection on the subsequent `depositPRC20WithAutoSwap` / `refundUnusedGas` swap) is computed as 95% of that same quote. Because the quote and the protected swap both read the identical, attacker-movable pool state, an unprivileged actor can move the pool price before the module's swap executes and profit from the resulting mis-priced conversion, at the expense of depositors' PRC20-to-PC conversions and protocol gas-refund accounting.

### Finding Description
Three call sites all follow the same pattern: fetch `fee` and `quote` from live on-chain state, derive `minPCOut = quote * 95 / 100`, then execute the real swap in the same keeper call via `DerivedEVMCall`:
- `ExecuteInboundGas` (TxType_GAS) [5](#0-4) 
- `gasAndPayloadDepositAutoSwap` (TxType_GAS_AND_PAYLOAD) [3](#0-2) 
- `applyGasRefund` / `getSwapQuoteForRefund` (outbound excess-gas refund) [4](#0-3) 

None of these paths use a time-weighted average price, an independent oracle, or a pool-liquidity floor gating eligibility for auto-swap. The slippage bound is entirely self-referential: it is 95% of whatever the pool's instantaneous price happens to be at execution time, so it cannot detect or prevent price distortion introduced immediately beforehand by an ordinary EVM transaction against the same public pool. This is the same invariant break as the source report: a value-conversion step trusts a pool state that an unprivileged actor can freely skew, with no minimum-liquidity or independent-price safeguard analogous to `MINIMUM_LIQUIDITY`.

### Impact Explanation
Every `GAS_AND_PAYLOAD`, `GAS` inbound and every outbound gas-fee refund that goes through the auto-swap path converts protocol-held/user PRC20 tokens into PC (or vice versa in refunds) at a price an unprivileged actor can bias in their own favor immediately beforehand, extracting up to the 5% slippage margin from each conversion. This directly corrupts gas-fee/refund accounting and the effective value received by depositors — falling under "corruption of ... gas fee accounting, refund accounting ... token mapping" in the allowed impact set. The exposure is worst for thinly liquidated PRC20 pools (freshly onboarded tokens), exactly mirroring the low-total-supply / skewed-ratio precondition from the original report.

### Likelihood Explanation
Medium — this requires only ordinary, unprivileged EVM transactions against the publicly readable/tradable Uniswap V3 pool referenced by `UniversalCore`, timed around the block in which the finalizing `MsgVoteInbound`/`MsgVoteOutbound` triggers the module's derived swap. No validator, TSS, or admin privilege is needed. Feasibility is inversely proportional to the target pool's real liquidity depth, and the protocol has no minimum-liquidity requirement gating which pools are eligible for auto-swap.

### Recommendation
Do not derive `minPCOut` solely from an instantaneous on-chain quote read in the same execution as the protected swap. Use a TWAP observation window from the pool (or an independent oracle) for the slippage floor, cap swap size relative to pool depth, and/or require a minimum-liquidity threshold on the underlying Uniswap V3 pool before it is eligible for protocol-driven auto-swap routing.

### Proof of Concept
1. Identify a PRC20 token registered in `uregistry` whose paired Uniswap V3 pool (returned by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`) has shallow liquidity.
2. Submit a large swap transaction against that pool in the same block window in which a pending `MsgVoteInbound`/`MsgVoteOutbound` is about to reach quorum and trigger `ExecuteInboundGas` / `applyGasRefund` (attacker observes pending UV votes in mempool as ordinary txs).
3. When the finalizing vote lands and the module computes `GetSwapQuote` → `minPCOut = quote*95/100` → `CallPRC20DepositAutoSwap`/`refundUnusedGas`, it executes against the attacker-skewed price, since both quote and swap read the same manipulated pool state.
4. Attacker reverses their initial swap immediately after (same or next block), capturing the price difference while the module's swap settled at the distorted rate, extracting up to 5% value from the affected deposit/refund. [1](#0-0)

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
