### Title
Spot-price Uniswap V3 quote (no TWAP) used to settle gas-token→PC swaps lets an attacker sandwich the auto-swap deposit and gas-refund paths to drain protocol-owned liquidity - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`)

### Summary
The external report describes an attacker manipulating an AMM's instantaneous pool balance to inflate a settlement amount (impermanent-loss reimbursement) computed from spot price, then reversing the manipulation. Push Chain's `x/uexecutor` keeper has a structurally identical pattern: it settles PRC20-gas-token→PC conversions (both the inbound "auto-swap deposit" path and the outbound "unused gas refund" path) using a live Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote, and derives its own slippage floor (`minPCOut`) from that same manipulated quote, so the slippage check provides no protection against pre-existing price distortion.

### Finding Description
`GetSwapQuote` in [1](#0-0)  calls `quoteExactInputSingle` on the UniversalCore-configured Uniswap V3 QuoterV2 pool to price a gasToken→WPC conversion at the current on-chain state — this is a spot-price read, not a time-weighted average.

This spot quote is consumed in two places where user/attacker-triggered actions cause real value to move:

1. **Inbound GAS deposit auto-swap.** In `ExecuteInboundGas`, the quote is fetched and `minPCOut` is set to 95% of that exact quote, then `CallPRC20DepositAutoSwap` executes the swap for real using that bound: [2](#0-1) . The same pattern repeats for `GAS_AND_PAYLOAD` in `gasAndPayloadDepositAutoSwap`: [3](#0-2) .

2. **Outbound excess-gas refund.** `applyGasRefund` computes `refundAmount = gasFee - gasFeeUsed` (values reported via `MsgVoteOutbound`), then fetches a spot quote via `getSwapQuoteForRefund` and again derives `minPCOut` as 95% of that same quote before calling `CallUniversalCoreRefundUnusedGas` with `withSwap=true`: [4](#0-3) .

In both flows, `minPCOut` is computed *from* the same spot quote that will be used for the swap — it only bounds intra-transaction slippage, not a price that an attacker has already pushed away from fair value in a preceding transaction. An unprivileged attacker who trades against the same Uniswap V3 pool (buying/selling the gasToken/WPC pair) immediately before their own inbound deposit is executed, or before the finalizing `MsgVoteOutbound` lands, can skew the pool price so the module's `quoteExactInputSingle` call returns an inflated `amountOut`. The protocol then executes `depositPRC20WithAutoSwap` / `refundUnusedGas` at that inflated rate, paying the attacker (or their designated `refundRecipient`/UEA) more PC than the token is genuinely worth, funded out of the pool's (protocol-owned) liquidity. The attacker then reverses their price-moving trade to recover their capital, net of pool fees — the same "manipulate → settle at bad price → restore" pattern as the Vader IL exploit, just with a swap-quote instead of an IL formula.

### Impact Explanation
This falls under "corruption of ... gas fee accounting, refund accounting ... token mapping" and "unauthorized ... release ... of ... protocol-controlled funds" in the allowed-impact gate: PC paid out by `depositPRC20WithAutoSwap`/`refundUnusedGas` is minted/transferred out of protocol-controlled liquidity based on a manipulable price oracle, letting an attacker extract value disproportionate to the gas token actually surrendered, at the expense of the pool/protocol.

### Likelihood Explanation
Likelihood is bounded by how easily/atomically an unprivileged actor can (a) move the Uniswap V3 pool price and (b) get their own trigger (a GAS inbound, or the vote that finalizes their outbound) executed in the same or adjacent block before the price reverts. The inbound/outbound execution point is decided by honest-UV vote quorum timing rather than the attacker's own Push Chain transaction, so exploitation requires either favorable timing/front-running around the quorum-crossing block or a thin/newly-configured gasToken pool where limited liquidity makes the spot price cheap to move. This is a realistic but not guaranteed-every-time condition, and profit is bounded by pool depth, swap fees, and the fixed `refundAmount`/deposit `amount` involved.

### Recommendation
Do not derive `minPCOut` from the same live spot quote used to execute the swap. Use a TWAP-based oracle (or a pre-agreed/oracle-sourced conversion rate) for pricing gasToken↔PC conversions, and/or cap the allowed deviation between the executed price and an independent reference price, so a single-block or adjacent-block price manipulation cannot be used to extract more PC than the gas token is genuinely worth.

### Proof of Concept
1. Attacker (unprivileged, ordinary user) holds gasToken and WPC and trades directly against UniversalCore's Uniswap V3 pool for the pair, moving the pool's spot price so that a subsequent `quoteExactInputSingle(gasToken → WPC)` call returns an inflated `amountOut`.
2. Attacker submits (or already has pending) a `GAS`/`GAS_AND_PAYLOAD` inbound deposit of gasToken, or is the recipient/`fund_recipient` of an outbound about to be finalized via `MsgVoteOutbound`.
3. When UV votes cross quorum in the same/adjacent block as step 1, `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` ( [5](#0-4) ) or `applyGasRefund` ( [4](#0-3) ) calls `GetSwapQuote` and executes the swap using the manipulated price, minting/transferring more PC to the attacker's UEA/recipient than fair value.
4. Attacker reverses the initial trade to restore the pool price and recoup their capital, keeping the excess PC as profit — mirroring the "manipulate, settle, restore" structure of the original Vader IL exploit.

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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L348-379)
```go
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
