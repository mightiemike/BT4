## Analysis

The external report's core theme — a contract computing a swap execution price using data that an "arbitrary" or attacker-influenced input can manipulate to extract value at the victim's expense (the `CurveExchange.swapFromBold` finding) — has a concrete analog in Push Chain's auto-swap paths inside `x/uexecutor`.

### Title
Unprotected same-block AMM quote enables sandwich extraction on inbound/outbound auto-swap paths - (`x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every place `x/uexecutor` swaps a bridged/gas token against WPC through the on-chain Uniswap V3 pool computes its slippage bound (`minPCOut`) from a spot quote fetched via `GetSwapQuote` (`CallEVM` with `commit=false`) in the *same* message execution that immediately performs the swap via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. Because the quote and the swap execute back-to-back within the same block, an unprivileged attacker who observes the pending `MsgVoteInbound`/`MsgVoteOutbound` transaction in the mempool can sandwich it: manipulate the pool price immediately before, letting the protocol's swap land at the manipulated price (bounded only by a flat 5% tolerance), then unwind for profit.

### Finding Description
`GetSwapQuote` ( [1](#0-0) ) reads the pool's current price via `QuoterV2.quoteExactInputSingle` with `commit=false`. Callers then apply a fixed 5% slippage band: [2](#0-1) 

and immediately execute the real swap through `CallPRC20DepositAutoSwap` using that `minPCOut`: [3](#0-2) 

The identical pattern (quote → 95% slippage bound → swap, all inside one message handler) is repeated in `gasAndPayloadDepositAutoSwap` ( [4](#0-3) ) for `GAS_AND_PAYLOAD` inbounds, and in `applyGasRefund`'s swap-refund leg for outbound gas refunds: [5](#0-4) 

`MsgVoteInbound` and `MsgVoteOutbound` are both in the gasless whitelist ( [6](#0-5) ), meaning UV votes for these operations are submitted as ordinary, cheap, publicly-visible mempool transactions. The quote is not computed at some earlier, unpredictable point (e.g., a prior block or a user-supplied minimum) — it is computed inline in the same call stack as the swap, so it reflects whatever the AMM pool state is at that instant, including any manipulation from a transaction ordered immediately before it in the same block.

An unprivileged attacker with no special role (not a UV, not an admin) can:
1. Watch the mempool for a `MsgVoteInbound`/`MsgVoteOutbound` transaction that is about to reach quorum (the last vote of `votingThreshold`), which is publicly identifiable since ballots and vote counts are on-chain state.
2. Submit a large swap against the same Uniswap V3 pool (prc20 ↔ WPC) to push the price against the direction the protocol will execute, front-running the finalizing vote.
3. Let the protocol's own swap (`depositPRC20WithAutoSwap` / `refundUnusedGas` with `withSwap=true`) execute at the manipulated price — it will still pass the 5% slippage check because that check is derived from the *manipulated* quote, not a pre-manipulation baseline.
4. Reverse the manipulation in a back-run transaction, capturing the value extracted from the protocol's swap as profit.

### Impact Explanation
Each sandwiched swap can extract up to the full width of the fixed 5% slippage tolerance from either (a) the bridging user's synthetic PC balance on `GAS`/`GAS_AND_PAYLOAD` inbounds, or (b) the excess-gas refund recipient's proceeds on outbound finalization. This is a repeatable, per-inbound/per-outbound value leak from protocol/user-controlled funds during universal execution and gas-fee-refund accounting — falling under "corruption of ... gas fee accounting, refund accounting ... " and unauthorized loss of user-controlled funds in the allowed-impact list.

### Likelihood Explanation
High for any inbound/outbound involving nonzero swap amounts. The trigger requires no privileged role: any address able to submit transactions to Push Chain and hold/borrow tokens for the target pool can execute the sandwich. UV votes being gasless and publicly broadcast makes the finalizing transaction easy to detect and front-run; no validator collusion or key compromise is required.

### Recommendation
Do not derive the slippage bound from a spot quote taken in the same transaction/block as the swap. Options: source `minPCOut` from a TWAP/oracle price rather than `quoteExactInputSingle` spot state, widen protection with a much smaller tolerance combined with a time-weighted reference, or move the quote fetch to a separate, earlier-committed step that a within-block attacker cannot influence at swap time (e.g., a quote captured at inbound-recording time rather than inbound-execution time, if that closes the gap sufficiently). At minimum, add circuit breakers that compare the spot quote against a longer-window oracle price and abort/queue for manual review on large deviations.

### Proof of Concept
1. Attacker monitors mempool for the UV vote transaction that will push a pending `GAS`/`GAS_AND_PAYLOAD` inbound (or outbound) over `votingThreshold`.
2. Attacker submits (same block, ordered before) a large swap on the relevant Uniswap V3 pool (prc20 ↔ WPC) moving the price by up to just under the tolerance the protocol will subsequently accept.
3. The UV vote tx executes `ExecuteInboundGas` → `GetSwapQuote` (reads the now-manipulated pool state) → `minPCOut = quote*95/100` → `CallPRC20DepositAutoSwap`, which succeeds at the manipulated rate.
4. Attacker submits a back-run swap reversing the price move, realizing the delta as profit at the expense of the bridging user's minted PC / the outbound gas-refund recipient.

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

**File:** x/uexecutor/keeper/evm.go (L542-593)
```go
func (k Keeper) CallPRC20DepositAutoSwap(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount, fee, minPCOut *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
	k.Logger().Debug("EVM call: depositPRC20WithAutoSwap",
		"prc20", prc20Address.Hex(),
		"recipient", to.Hex(),
		"amount", amount.String(),
		"fee", fee.String(),
		"min_pc_out", minPCOut.String(),
	)
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse Handler Contract ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	// Before sending an EVM tx from module
	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	// increment first (safe for internal modules)
	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

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
}
```

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

**File:** app/txpolicy/gasless.go (L17-26)
```go
		GaslessMsgTypes = []string{
			sdk.MsgTypeURL(&uexecutortypes.MsgMigrateUEA{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgExecutePayload{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteInbound{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteOutbound{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteTssKeyProcess{}),
			sdk.MsgTypeURL(&utsstypes.MsgVoteFundMigration{}),
			sdk.MsgTypeURL(&uexecutortypes.MsgVoteChainMeta{}),
		}
	)
```
