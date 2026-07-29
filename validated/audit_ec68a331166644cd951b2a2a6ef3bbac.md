Confirmed: the pools (WPC ↔ synthetic token) are standard Uniswap V3 pools created with real, tradeable liquidity [1](#0-0) , and both the "quote" and the "minimum acceptable output" for every protocol-initiated swap are derived from the *same* live `QuoterV2.quoteExactInputSingle` spot-price call taken immediately before the swap executes [2](#0-1) , with only a fixed 5% band applied as the "slippage protection" [3](#0-2) [4](#0-3) .

### Title
Protocol-executed PRC20↔PC auto-swaps (gas top-up and gas-refund flows) use a self-referential spot-price quote as both market price and slippage bound, enabling AMM price-manipulation to steal value from the swap pool - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
The external report's bug class is "a value paid out to a user is computed from a metric that does not reflect the true, non-gameable committed value (staked amount), so an attacker can manipulate the metric to receive a distorted reward." The Push Chain analog is in the PRC20⇄PC auto-swap paths (`GAS` inbound top-ups and outbound gas refunds): the amount of PC the protocol will accept from its own Uniswap V3 pool is computed entirely from a spot-price quote fetched moments before the swap is executed, and that same manipulable quote is also used to derive the slippage floor, so the "protection" moves with the manipulation instead of resisting it.

### Finding Description
Three call sites — `ExecuteInboundGas` (GAS inbound top-up) [5](#0-4) , `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound) [6](#0-5) , and `applyGasRefund` (outbound unused-gas refund) [7](#0-6)  — all follow the identical pattern:

1. Call `GetSwapQuote`, which invokes `QuoterV2.quoteExactInputSingle` against the live Uniswap V3 pool state at the current block [2](#0-1) .
2. Compute `minPCOut = quote * 95 / 100` — a 5% band around that same quote [8](#0-7) .
3. Immediately call `depositPRC20WithAutoSwap` / `refundUnusedGas` on `UniversalCore`, which performs the actual swap against the same pool, bound only by that self-derived `minPCOut` [9](#0-8) [10](#0-9) .

Because the quote and the swap read the same pool reserves within the same protocol-driven flow, an unprivileged external actor who can trade against the WPC/PRC20 pool (the pools are ordinary Uniswap V3 pools seeded with real liquidity as part of normal deployment, e.g. WPC/pSOL [1](#0-0) ) can push the spot price in a favorable direction immediately before the protocol's own swap executes. The `minPCOut` floor is computed from the *already-manipulated* price, so it provides no real protection — the "slippage bound" simply follows the manipulation rather than resisting it (this is the classic anti-pattern of using an AMM spot price as both the price source and its own sanity check, instead of an independent/TWAP reference). There is no use of `quoteExactInputSingle`'s time-weighted variant, no oracle cross-check, and no cap on price impact independent of the manipulated quote itself.

This is directly analogous to the external report's bug class: instead of basing an economic payout on a true, hard-to-manipulate quantity (staked capital / real reserve depth over time), the code bases it on an instantaneous, attacker-influenceable number (spot quote), and that same number is reused to validate itself.

### Impact Explanation
An attacker can sandwich the protocol's auto-swap calls (which fire deterministically once quorum inbound/outbound votes finalize) to extract value from the WPC/PRC20 pool: manipulate the pool price prior to the protocol's swap so it receives fewer PC per unit of PRC20/gas token than the honest market price would dictate, capturing the difference during the attacker's own reverse trade. Because `depositPRC20WithAutoSwap`/`refundUnusedGas` move real PRC20/PC out of protocol-controlled liquidity and into user-controlled UEAs, this corrupts PRC20/native asset accounting and gas-refund accounting — falling under "corruption of ... gas fee accounting, refund accounting ... or unauthorized [...] refund of user or protocol-controlled funds" in the allowed impact gate. The loss is bounded per-transaction by the 5% band and pool depth, but is repeatable on every `GAS`/`GAS_AND_PAYLOAD` inbound and every outbound gas refund, and requires no privileged role — any trader with capital to move the pool can execute it.

### Likelihood Explanation
Medium. It requires: (1) the attacker to identify or trigger a pending `GAS`/`GAS_AND_PAYLOAD` inbound or an outbound about to be voted to quorum (both are publicly observable on-chain events/votes before execution), and (2) sufficient capital relative to pool depth to move the spot price meaningfully within the 5% band. Thinly-liquidity pools (typical for newly-launched PRC20/WPC pairs, as seeded in the e2e setup with modest amounts) make this materially easier. No validator, admin, or TSS compromise is needed.

### Recommendation
Do not derive the slippage floor from the same instantaneous quote used to execute the swap. Use a manipulation-resistant reference price instead — e.g., a time-weighted average price (TWAP) read from the Uniswap V3 pool's oracle observations, or an independently-configured price bound (e.g., registry-provided min-acceptable-rate) — and compare the live quote against that independent reference before proceeding, aborting/falling back to the no-swap deposit path when the live quote deviates beyond an acceptable threshold from the TWAP. Additionally consider batching/delaying protocol-driven swaps or capping per-swap notional relative to pool depth to limit sandwich profitability.

### Proof of Concept
1. Attacker observes a pending `GAS` inbound (or an outbound about to reach 2/3 UV vote quorum) for token X, whose PRC20/WPC pool has liquidity depth L.
2. Immediately before/at the block where `ExecuteInboundGas` (or `applyGasRefund`) will run, attacker submits a large swap against the same Uniswap V3 pool to move the spot price against the protocol's upcoming trade direction.
3. `GetSwapQuote` reads the now-manipulated pool state and returns a distorted `quote`; `minPCOut = quote*95/100` inherits the same distortion [11](#0-10) .
4. `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap against the manipulated pool, and since `minPCOut` was computed from that same manipulated price, the trade proceeds even though it delivers a worse rate than fair market value.
5. Attacker reverses their initial trade in the same or a following block, netting the price-impact spread extracted from the protocol-owned liquidity, at the expense of the recipient's expected PC amount and/or the pool's reserves.

### Citations

**File:** e2e-tests/setup.sh (L4550-4568)
```shellscript

  while IFS=$'\t' read -r token_symbol token_addr; do
    [[ -n "$token_addr" ]] || continue
    if [[ "$(echo "$token_addr" | tr '[:upper:]' '[:lower:]')" == "$(echo "$wpc_addr" | tr '[:upper:]' '[:lower:]')" ]]; then
      continue
    fi

    local pool_token_amount="1"
    local pool_wpc_amount="4"
    if [[ "$token_symbol" == "pSOL" ]]; then
      pool_token_amount="${LOCAL_PSOL_POOL_TOKEN_AMOUNT:-50}"
      pool_wpc_amount="${LOCAL_PSOL_POOL_WPC_AMOUNT:-200}"
    fi

    log_info "Creating ${token_symbol}/WPC pool with liquidity (${pool_token_amount}/${pool_wpc_amount})"
    (
      cd "$SWAP_AMM_DIR"
      node scripts/pool-manager.js create-pool "$token_addr" "$wpc_addr" 4 500 true "$pool_token_amount" "$pool_wpc_amount"
    )
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

**File:** x/uexecutor/keeper/evm.go (L624-643)
```go
	// fee is uint24 in Solidity — pass as *big.Int (go-ethereum ABI packs non-standard widths as *big.Int)
	return k.evmKeeper.DerivedEVMCall(
		ctx,
		abi,
		ueModuleAccAddress,
		handlerAddr,
		big.NewInt(0),
		nil,
		true,
		false,
		true,
		&nonce,
		"refundUnusedGas",
		gasToken,
		amount,
		recipient,
		withSwap,
		fee,
		minPCOut,
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

**File:** x/uexecutor/keeper/outbound.go (L174-257)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

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

	// Step 2: fallback — refund without swap (deposit PRC20 directly to recipient)
	ctx.Logger().Error("applyGasRefund: swap refund failed, falling back to no-swap",
		"outbound_id", outbound.Id,
		"reason", swapFallbackReason,
	)

	resp, err := k.CallUniversalCoreRefundUnusedGas(ctx, gasToken, refundAmount, recipientAddr, false, big.NewInt(0), big.NewInt(0))
	if err != nil {
		refundPcTx.Status = "FAILED"
		refundPcTx.ErrorMsg = err.Error()
	} else {
		refundPcTx.TxHash = resp.Hash
		refundPcTx.GasUsed = resp.GasUsed
		refundPcTx.Status = "SUCCESS"
	}

	outbound.PcRefundExecution = refundPcTx
	outbound.RefundSwapError = swapFallbackReason
}
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
