## Analog Identified: Spot-Price AMM Quote Used for Instant Value-Bearing Swaps (No TWAP Protection)

### Title
Unprotected spot-price Uniswap V3 quote enables sandwich-attack draining of PRC20/WPC deposit-swap and gas-refund paths - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The DeFiner report's root cause is that an *instantaneous* price reading is trusted to gate an *instant* fund movement, with only a fixed tolerance band and no resistance to price manipulation. Push Chain's `GAS`/`GAS_AND_PAYLOAD` inbound-deposit auto-swap and the outbound gas-refund swap reproduce this pattern: they call Uniswap V3's `QuoterV2.quoteExactInputSingle` for a live spot-price quote and derive `minPCOut` as a flat 95% of that same quote, then immediately execute the swap in the same keeper flow.

### Finding Description
`Keeper.GetSwapQuote` calls the Uniswap V3 `QuoterV2` contract to obtain `amountOut` for a `prc20 → WPC` swap using the pool's current spot price (`sqrtPriceX96`), with no time-weighted averaging: [1](#0-0) 

`ExecuteInboundGas` (GAS route) and `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD route) both fetch this instantaneous quote and derive `minPCOut` as a flat 5% slippage band off it, then execute `CallPRC20DepositAutoSwap` in the very same call chain: [2](#0-1) [3](#0-2) 

The outbound gas-refund path (`applyGasRefund` → `getSwapQuoteForRefund`) follows the identical pattern for the `gasToken → WPC` refund leg: [4](#0-3) [5](#0-4) 

Because both the quote and the swap execute atomically within the module's own keeper flow (during block execution, not a user-submitted mempool transaction), the quote itself cannot be manipulated between fetch and execution. However, since these deposits/refunds are processed deterministically as a direct consequence of an ordinary, unprivileged user's own inbound transaction (or a natural gas-refund trigger after outbound observation), an attacker can predict the block in which their own inbound's auto-swap will execute and pre-position an AMM price move against the underlying `prc20/WPC` (or `gasToken/WPC`) Uniswap V3 pool in an adjacent transaction within the same block (front-run), causing `quoteExactInputSingle` to report a skewed spot price at execution time, then reverse the position after (back-run) to capture the difference. The 5% band is computed *from the same manipulated spot price*, so it provides no protection against this — it only bounds slippage relative to an already-corrupted reference point, exactly mirroring the DeFiner pattern where "borrow power" and "collateral value" are both derived from the same volatile, unprotected price source used for immediate execution.

### Impact Explanation
Successful manipulation lets an unprivileged attacker extract value from the protocol's owned `WPC`/`PRC20` Uniswap V3 liquidity during auto-swap deposits or gas refunds — a drain of protocol- or pool-controlled funds reachable purely through ordinary deposit/inbound submission and AMM interaction, satisfying the "draining ... of protocol-controlled funds" and "corruption of PRC20 or native asset accounting" impact categories.

### Likelihood Explanation
Likelihood depends on the depth/liquidity of the specific `prc20/WPC` and `gasToken/WPC` pools selected via `GetDefaultFeeTierForToken`; for low-liquidity or newly-listed PRC20 pairs, moving the spot price within a single block is inexpensive. The attack requires no validator or admin privilege — only submitting an inbound deposit (or waiting for a natural gas refund) and executing surrounding swap transactions on the same external AMM pool that backs `WPC`.

### Recommendation
Replace the direct spot-price `quoteExactInputSingle` call with a TWAP-based reference price (e.g., Uniswap V3 `observe`/oracle over a meaningful window) for computing `minPCOut`, or clamp the allowed deviation between spot price and a longer-window TWAP before accepting the swap, rather than deriving the tolerance band purely from the instantaneous quote itself.

### Proof of Concept
1. Attacker identifies a `prc20`/`WPC` (or `gasToken`/`WPC`) Uniswap V3 pool with limited liquidity used by `GetSwapQuote`.
2. Attacker submits a source-chain gateway deposit that will trigger a `GAS`/`GAS_AND_PAYLOAD` inbound once validators vote it in (`ExecuteInboundGas` / `gasAndPayloadDepositAutoSwap`), or triggers an outbound whose refund path (`applyGasRefund`) will fire on observation.
3. In the same Push Chain block where the module-driven auto-swap/refund executes, the attacker (via another controlled contract/tx capable of interacting with the pool in that block, or via known validator execution timing) skews the pool's spot price just before the `GetSwapQuote` call, and reverses it after, capturing the value difference between the manipulated `minPCOut` bound and the pool's equilibrium price. [1](#0-0) [6](#0-5)

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
