## Analog Found

### Title
Sandwichable spot-price swap in gas-abstraction auto-swap drains user deposit value — ([File: x/uexecutor/keeper/evm.go], [File: x/uexecutor/keeper/execute_inbound_gas.go], [File: x/uexecutor/keeper/execute_inbound_gas_and_payload.go])

### Summary
The Notional report describes an attacker who briefly manipulates a rate to profit before a slow correction mechanism catches up. Push Chain's gas-abstraction deposit flow (`GAS` / `GAS_AND_PAYLOAD` inbound routes) has a direct analog: the module fetches a single instantaneous spot quote from the on-chain Uniswap V3 `QuoterV2` and immediately executes the swap with a fixed 5% slippage tolerance, with no TWAP, no cooldown, and no per-block price-deviation guard.

### Finding Description
When an inbound deposit is finalized (ballot quorum reached via `MsgVoteInbound`), `x/uexecutor` executes `k.GetSwapQuote` (a static call to `QuoterV2.quoteExactInputSingle`) and then, essentially in the very next step of the same keeper call, executes `k.CallPRC20DepositAutoSwap` with `minPCOut = quote * 95 / 100`: [1](#0-0) [2](#0-1) 

The same pattern repeats for `GAS_AND_PAYLOAD` in `gasAndPayloadDepositAutoSwap`: [3](#0-2) 

and for outbound gas refunds: [4](#0-3) 

Both `GetSwapQuote` and `CallPRC20DepositAutoSwap` read/act on the *same* on-chain WPC/PRC20 liquidity pool at essentially the same instant. The only defense against price manipulation is the 5% slippage band computed from that same manipulated instantaneous price — this is structurally the "borrow enough to move the rate" scenario from the Notional report, except here it is a spot AMM price instead of an interest-rate oracle, and there is no TWAP-style correction window at all (Notional at least had an hour-long averaging window; here there is none).

An unprivileged attacker can:
1. Observe a pending `MsgVoteInbound` that will trigger a GAS/GAS_AND_PAYLOAD deposit auto-swap (or simply predict/trigger their own inbound).
2. Submit an ordinary EVM transaction against the same WPC/PRC20 pool with higher priority to shift the spot price immediately before the module's `GetSwapQuote` call executes in the same block.
3. Let the module's `GetSwapQuote`+`CallPRC20DepositAutoSwap` execute at the manipulated price (protected only by 5% slippage around the manipulated number, which is trivially satisfied since the attacker controls the price used to derive that band).
4. Reverse the manipulation trade afterward, capturing the price impact as profit, extracted from the value that should have gone to the depositing user's UEA as PC.

### Impact Explanation
This directly drains value from user deposits during the protocol's own gas-abstraction auto-swap, i.e., unauthorized loss of user-controlled funds during a default, unprivileged, user-reachable deposit path (`ExecuteInboundGas` / `ExecuteInboundGasAndPayload`), matching the in-scope impact "stealing, draining, permanent loss ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting."

### Likelihood Explanation
Feasibility depends on: liquidity depth of the WPC/PRC20 pool relative to attacker capital, and the attacker's ability to land transactions in the desired order within a block (standard MEV/sandwich conditions, not requiring any validator or admin privilege). This mirrors the original report's judged "medium" severity — the attack requires capital and specific conditions to align, but no privileged access, and Notional's own accepted mitigation (widen the TWAP window) has no counterpart at all in this Push Chain code (single spot quote, immediate use).

### Recommendation
- Replace the single spot `QuoterV2` quote with a time-weighted or multi-sample price reference (or an off-chain/validator-attested reference price) before computing `minPCOut`.
- Widen slippage tolerance dynamically based on observed pool depth, or cap the swap size relative to pool liquidity.
- Consider using a commit-reveal or per-block price-deviation cap on the WPC/PRC20 pools used for gas abstraction so a single-block manipulation cannot be profitably sandwiched.

### Proof of Concept
1. Attacker monitors mempool/relayer-broadcast `MsgVoteInbound` transactions destined to finalize a large GAS/GAS_AND_PAYLOAD inbound (or triggers their own large inbound).
2. Attacker submits a large swap against the target PRC20/WPC pool (same block, higher gas price) to move the spot price.
3. The finalizing `MsgVoteInbound` tx executes `GetSwapQuote` (now reading the manipulated price) then `CallPRC20DepositAutoSwap` with `minPCOut` derived from that manipulated quote — the swap executes at the bad price, since 5% tolerance is computed from the already-manipulated number.
4. Attacker submits a reversing trade to restore the pool price and realize the price-impact profit, at the expense of the value the depositing user's UEA should have received. [5](#0-4)

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L134-153)
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
							if execErr != nil {
								shouldRevert = true
								revertReason = execErr.Error()
							}
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
