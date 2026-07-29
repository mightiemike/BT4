Confirmed: the Uniswap V3 pool (PRC20/WPC) and QuoterV2 are permissionless on-chain contracts with real liquidity (per `e2e-tests/setup.sh` `step_setup_swap_amm` / `create-pool` deploying real Uniswap v3-core/periphery pools), meaning any unprivileged actor can trade against them to move the spot price before a validator-driven swap executes.

### Title
Inbound gas-deposit auto-swap and gas-refund use manipulable Uniswap V3 spot quote as both price reference and slippage bound, enabling sandwich attacks - ([File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go, x/uexecutor/keeper/evm.go])

### Summary
Push Chain's `ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund` all derive the `minPCOut` slippage bound from the *same* on-chain Uniswap V3 `QuoterV2.quoteExactInputSingle` spot-price call that produces the "expected" output amount, exactly the anti-pattern flagged in the external report (deriving both the expected repayment and the "available"/slippage-checked amount from the same manipulable AMM price). Since the slippage bound is computed from the manipulated price itself rather than an independent, time-resistant reference, it cannot detect or prevent a price manipulated by an attacker in the same block window.

### Finding Description
`GetSwapQuote` calls `quoteExactInputSingle` with `SqrtPriceLimitX96: 0`, i.e., an unrestricted, current-spot-price quote against the live Uniswap V3 pool: [1](#0-0) 

This quote is then used to compute `minPCOut` as a flat 95% of the quote (`minPCOut = quote * 95 / 100`) in three places:

1. `ExecuteInboundGas` (GAS inbound route): [2](#0-1) 
2. `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound route): [3](#0-2) 
3. `applyGasRefund` (post-outbound gas refund swap): [4](#0-3) 

All three then call `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`, which perform the actual on-chain swap against `minPCOut`: [5](#0-4) 

Because `minPCOut` is derived from the *same* spot price used for the swap execution (not an independent TWAP oracle or a caller-supplied, pre-manipulation-committed bound), an attacker who moves the pool price down immediately before the ballot-triggered execution, and moves it back immediately after, can force the deposit/refund swap to execute at an artificially depressed price while still satisfying the 5% band, since the band itself shifts with the manipulated price. The upgrade notes confirm this quoter-based 5% slippage replaced a previous "0-slippage" call, implying the developers were aware slippage protection was needed but chose a self-referential band rather than an oracle-independent one: [6](#0-5) 

The pool itself is a standard, permissionless Uniswap V3 pool with real liquidity that anyone can trade against, as set up in the e2e tooling: [7](#0-6) 

### Impact Explanation
This is the same bug class as the external NomadFacet report: the amount ultimately delivered to (or refunded to) a user/UEA is computed from a spot AMM price that an unprivileged actor can freely manipulate around the block in which the swap executes, and the "slippage protection" does not actually protect against this because it is derived from the same manipulated number. Every GAS or GAS_AND_PAYLOAD inbound deposit, and every excess-gas refund on outbound completion, is subject to value extraction by a sandwiching attacker: the depositing user's UEA receives less native PC than fair value, and/or the refund recipient receives less than the true excess gas fee, with the difference captured by the attacker's front-run/back-run trades. This directly corrupts native asset accounting for gas-funding and refund flows reachable by ordinary unprivileged user deposits (in scope: "corruption of PRC20 or native asset accounting ... refund accounting").

### Likelihood Explanation
Reachable by any unprivileged actor: they need only (a) submit an ordinary GAS/GAS_AND_PAYLOAD inbound deposit (or trigger any outbound with excess gas refund), and (b) hold or borrow assets to swap against the same permissionless Uniswap V3 pool before/after the deposit is voted in and executed by honest validators. No validator, node, or TSS collusion is required — this works purely through the public AMM and the deterministic execution timing of `VoteInbound` finalization / `VoteOutbound` finalization.

### Recommendation
Do not derive the slippage bound (`minPCOut`) from the same spot quote used to price the swap. Use a manipulation-resistant reference such as a Uniswap V3 TWAP observation over a sufficiently long window, or an independent price oracle, to compute `minPCOut`, and/or bound the maximum price impact allowed for a single quoter call by comparing it against a stored/oracle reference price rather than a percentage of itself.

### Proof of Concept
1. Attacker holds PRC20 and WPC and observes the mempool/consensus state for a pending `GAS`/`GAS_AND_PAYLOAD` inbound vote about to reach 2/3 quorum and finalize (or triggers their own inbound deposit and waits for finalization).
2. Immediately before the block in which `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` runs, attacker swaps a large amount of WPC into the PRC20/WPC pool (or PRC20 out), depressing the PRC20→WPC price.
3. `GetSwapQuote` returns a depressed `quote`; `minPCOut = quote * 95/100` is computed from this same depressed price.
4. `CallPRC20DepositAutoSwap` executes the deposit swap at the depressed price, satisfying `minPCOut` trivially since the bound was computed from the manipulated price itself; the UEA receives less WPC(native PC) than it should.
5. Attacker immediately reverses their trade, restoring the pool price and capturing the value difference extracted from the victim's deposit (or from the gas refund recipient in the `applyGasRefund` path).

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

**File:** x/uexecutor/keeper/evm.go (L540-593)
```go
// Calls Handler Contract to deposit prc20 tokens with auto-swap.
// fee and minPCOut must be pre-computed by the caller (see GetDefaultFeeTierForToken / GetSwapQuote).
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

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L369-378)
```go
	quote, err := k.GetSwapQuote(sdkCtx, quoterAddr, prc20AddressHex, wpcAddr, fee, amount)
	if err != nil {
		return nil, err
	}

	// 5% slippage: minPCOut = quote * 95 / 100
	minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
	minPCOut.Div(minPCOut, big.NewInt(100))

	return k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```

**File:** e2e-tests/setup.sh (L4551-4569)
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
  done < <(jq -r '.tokens[]? | [.symbol, .address] | @tsv' "$DEPLOY_ADDRESSES_FILE")
```
