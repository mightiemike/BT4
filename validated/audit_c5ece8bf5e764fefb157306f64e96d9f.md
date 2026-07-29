## Analysis

The external report's bug class — **executing a DEX swap without deriving the minimum-return bound from a price source independent of the swap itself, enabling MEV sandwich extraction** — has a direct analog in Push Chain's `x/uexecutor` gas-abstraction and refund-swap logic.

### Title
Same-block spot-price quoting lets MEV searchers sandwich the protocol's own PRC20→WPC autoswap and gas-refund swap - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
Every gas-abstraction inbound (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`) and every gas-refund outbound (`applyGasRefund`) that needs to convert a PRC20 gas token into PC computes `minPCOut` by calling `GetSwapQuote` (a live Uniswap V3 `QuoterV2.quoteExactInputSingle` read against the actual pool) and then applying a flat 5% haircut, immediately before executing the real swap through `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas` in the very same message execution.

### Finding Description
`k.GetSwapQuote` [1](#0-0)  queries the live Uniswap V3 QuoterV2 contract for the current pool price at execution time. Immediately afterward, `minPCOut` is derived purely as `quote * 95 / 100` and passed straight into the real swap call: [2](#0-1)  for `ExecuteInboundGas`, an identical pattern in `gasAndPayloadDepositAutoSwap` [3](#0-2) , and again in `applyGasRefund`'s swap-refund branch [4](#0-3) .

This swap path is triggered synchronously inside `VoteInbound`'s message handler as soon as the last validator vote reaches quorum [5](#0-4) , i.e. inside an ordinary `MsgVoteInbound` transaction included in a normal block. The Uniswap V3 pool that `QuoterV2`/`SwapRouter` trade against is a standard, publicly tradeable AMM deployed alongside the rest of the periphery contracts [6](#0-5) , reachable by any unprivileged EVM account.

Because the "minimum acceptable output" is computed from the *same* pool's spot price read at the *same* point in block execution as the swap itself, the 5% tolerance provides no protection against price manipulation: an unprivileged MEV searcher can submit an ordinary large swap against the PRC20/WPC pool immediately before the `MsgVoteInbound` (or any other tx) that will trigger the quorum-crossing vote, pushing the spot price down; `GetSwapQuote` then returns an already-depressed quote, `minPCOut` is computed against that depressed price (so it still "passes"), the module's real swap executes at the manipulated price, and the attacker back-runs with an opposite swap to restore the price and capture the spread. No malicious validator, relayer, or privileged actor is required — the attacker only needs to win normal transaction ordering in a block, exactly analogous to the Curve/Yearn sandwich in the source report, except here the reference price and the execution price are the same manipulable value instead of there being no minimum-return at all.

### Impact Explanation
Every user whose inbound gas top-up, gas+payload deposit, or unused-gas refund routes through the PRC20→WPC autoswap is exposed to value extraction on each such swap. Because these swaps execute as module-authorized `DerivedEVMCall`s tied to real user deposits and refunds, this is a direct value leak (draining) of protocol/user-controlled funds reachable purely from an unprivileged attacker's ordinary transaction submission, matching the "stealing / draining ... user or protocol-controlled funds" allowed impact.

### Likelihood Explanation
The trigger condition (spot price manipulation immediately surrounding a quorum-finalizing vote or refund outbound) requires only capital and normal transaction submission against a public Uniswap V3 pool — no validator collusion, no relayer compromise, and no privileged access. Given inbound/outbound processing timing is observable on-chain (pending ballots, vote counts), an attacker can predict when the quorum-crossing vote (and hence the swap) will land and time the sandwich accordingly.

### Recommendation
Do not derive `minPCOut` from a spot-price read taken in the same execution context as the swap. Use a manipulation-resistant reference price (e.g., a TWAP oracle, or a governance/registry-configured price with a bounded staleness and deviation check) independent of the instantaneous pool state, and/or cap per-swap notional size so a single block's price impact cannot be pushed far from the reference price.

### Proof of Concept
1. Attacker observes a `PendingInbound` entry for `TxType_GAS` nearing quorum (3rd of 4 validator votes still outstanding) via public query of `PendingInbounds`.
2. Attacker submits (with higher priority/gas) a large `swapExactInputSingle` against the PRC20/WPC Uniswap V3 pool used by `GetSwapQuote`, depressing the WPC-out price for that PRC20.
3. The quorum-completing `MsgVoteInbound` lands in the same or next block; `ExecuteInboundGas` calls `GetSwapQuote` [7](#0-6)  which reads the now-manipulated pool price, computes `minPCOut = quote*95/100` [8](#0-7) , and executes `CallPRC20DepositAutoSwap` at the depressed price — the user's UEA receives less PC than fair value.
4. Attacker back-runs with the opposite swap, restoring the pool price and capturing the spread, repeatable for every gas top-up/refund swap.

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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L136-155)
```go
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}

	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}
```

**File:** e2e-tests/setup.sh (L4021-4037)
```shellscript
  ) 2>&1 | tee "$periphery_log"

  local swap_router quoter_v2 position_manager
  swap_router="$(grep -E 'SwapRouter' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  quoter_v2="$(grep -E 'QuoterV2' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  position_manager="$(grep -E 'PositionManager' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"
  wpc_addr="$(grep -E '^.*WPC:' "$periphery_log" | grep -Eo '0x[a-fA-F0-9]{40}' | tail -1 || true)"

  [[ -n "$swap_router" ]] && record_contract "SwapRouter" "$swap_router"
  [[ -n "$quoter_v2" ]] && record_contract "QuoterV2" "$quoter_v2"
  [[ -n "$position_manager" ]] && record_contract "PositionManager" "$position_manager"
  [[ -n "$wpc_addr" ]] && record_contract "WPC" "$wpc_addr"

  assert_required_addresses

  log_ok "Swap AMM setup complete"
}
```
