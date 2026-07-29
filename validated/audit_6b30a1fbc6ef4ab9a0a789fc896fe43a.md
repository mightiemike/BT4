## Analysis

The external report's bug class is: **a critical fund-calculation depends on a manipulable AMM spot price with no freshness/staleness check and no independent time-weighted deviation check**, so an attacker can skew the price used for value transfer just before the protected transaction executes.

Push Chain's `x/uexecutor` module has a structurally identical pattern in the **gas-token → PC autoswap** path used when processing `GAS` / `GAS_AND_PAYLOAD` inbounds and the excess-gas refund path on outbound finalization.

- `k.GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` — a pure **spot-price** simulation against the live Uniswap V3 pool reserves at the current block, with no TWAP window and no historical/oracle cross-check. [1](#0-0) 
- The resulting `quote` is then used to derive the *only* protection value, `minPCOut = quote * 95 / 100`, i.e., the safety bound is computed from the very same manipulable spot price it's meant to guard against. [2](#0-1) [3](#0-2) 
- The same quote→swap coupling is repeated in the outbound gas-refund flow. [4](#0-3) 
- The actual value-moving swap is executed via `depositPRC20WithAutoSwap` / `refundUnusedGas` on `UniversalCore`, using the module account, and mutates real PRC20/PC balances. [5](#0-4) [6](#0-5) 
- This is a deliberate, documented design change (moving from 0-slippage to a flat 5% Uniswap-V3-Quoter-derived slippage), confirming there is no TWAP/oracle-deviation safeguard by design. [7](#0-6) 

This satisfies the "Registry and accounting path" / "PRC20 accounting" allowed-impact criteria and requires no privileged actor — any unprivileged address that can trade against the WPC/PRC20 Uniswap V3 pool (a normal, user-reachable action) can time a swap around the deterministic inbound/outbound execution to skew the quote-then-swap sequence, unlike the `MsgVoteChainMeta` gas-price oracle (which is UV-only/gasless-restricted and thus out of scope).

### Title
Gas-token autoswap uses an unprotected spot AMM quote as both the price source and its own slippage bound, enabling sandwich extraction of PRC20/PC value - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, and `applyGasRefund` all derive `minPCOut` from `GetSwapQuote`, a live spot-price read of the Uniswap V3 `QuoterV2.quoteExactInputSingle` on the WPC/PRC20 pool, taken in the same block as the protected `depositPRC20WithAutoSwap` / `refundUnusedGas` swap. There is no TWAP, no `last_prices_timestamp`/freshness check, and no independent deviation check against a longer-window reference price — the only guard (`quote * 95%`) is computed from the exact value an attacker can move.

### Finding Description
`GetSwapQuote` reads the pool's current state via `quoteExactInputSingle` with `SqrtPriceLimitX96 = 0` (unbounded), i.e., whatever the pool's instantaneous price is. [8](#0-7) 
That single spot read is used to compute `minPCOut` and is immediately followed by the real value-moving swap inside the same keeper call sequence: [9](#0-8) 
Because `minPCOut` is 95% of the *same* spot quote (not a check against a time-weighted/oracle reference), an attacker who moves the pool price in a transaction ordered just before the inbound-vote-finalizing transaction (or the outbound-vote-finalizing transaction for the refund path) can:
1. Push the pool price so the quote/`minPCOut` reflects the manipulated state.
2. Let the module's autoswap execute against that same manipulated state (both reads happen deterministically within the block the finalizing vote lands in, with no cooldown or TWAP smoothing).
3. Reverse the manipulation afterward, extracting the difference as a classic sandwich, bounded only by the 5% band which itself was derived from the manipulated price rather than a trustworthy baseline.

This differs from the `ChainMeta`/`GasPrice` oracle (`MsgVoteChainMeta`), which is restricted to bonded Universal Validators and thus out of scope for an unprivileged attacker; the Uniswap V3 pool itself is a normal, permissionlessly tradable pool.

### Impact Explanation
Successful exploitation misroutes PRC20/native value during PRC20-to-PC autoswaps performed by the `uexecutor` module on behalf of users (gas top-up deposits) and during excess-gas refunds — corrupting PRC20/native asset accounting and extracting value that should have gone to the depositing user or been retained by protocol-controlled liquidity, matching the "corruption of PRC20 or native asset accounting" and "unauthorized … refund" allowed impacts.

### Likelihood Explanation
Requires only that the attacker be able to submit an ordinary swap transaction on the relevant Uniswap V3 pool timed around a UV-quorum-triggering vote transaction (publicly observable in the mempool/execution flow) — no validator, admin, or key compromise needed. The 5% band is a small, calculable amount per pool depth, making the attack directly profitable when pool liquidity is thin relative to the deposit/refund amount, mirroring the original report's finding that liquidity-comparable manipulation is realistic even without flash loans.

### Recommendation
- Replace or supplement the spot `quoteExactInputSingle` read with a TWAP-based price (e.g., Uniswap V3 `observe`) over a meaningful window, and reject execution if the pool's recent price has moved beyond a bounded deviation from that TWAP.
- Do not derive the slippage floor (`minPCOut`) purely from the same spot quote being protected against; compare the spot price to an independent time-weighted reference and abort (falling back to the no-swap path) if the deviation exceeds a safe threshold.
- Consider widening or making configurable the swap execution window relative to the block in which manipulation could occur, and/or add a minimum liquidity/TWAP staleness check before any autoswap or refund-swap is attempted.

### Proof of Concept
1. Identify a chain-token whose `GAS`/`GAS_AND_PAYLOAD` inbound processing (or outbound gas refund) routes through `gasAndPayloadDepositAutoSwap`/`applyGasRefund`, using a WPC/PRC20 Uniswap V3 pool with limited depth.
2. Monitor the mempool/chain for a `MsgVoteInbound` (or `MsgVoteOutbound`) transaction that will supply the final vote needed to reach UV quorum and trigger `ExecuteInboundGas`/`handleSuccessfulOutbound`.
3. Submit a large swap on the WPC/PRC20 pool ordered immediately before that finalizing vote transaction lands, shifting the pool's instantaneous price.
4. When the finalizing transaction executes, `GetSwapQuote` reads the manipulated spot price, computes `minPCOut = quote * 0.95`, and `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap against the still-manipulated pool state.
5. Reverse the initial swap in a following transaction, realizing the extracted value (bounded by the 5% band, computed from the attacker-controlled price) as sandwich profit at the expense of the depositing user / protocol-controlled liquidity.

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

**File:** x/uexecutor/keeper/evm.go (L595-644)
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
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	abi, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	nonce, err := k.GetModuleAccountNonce(ctx)
	if err != nil {
		return nil, err
	}

	if _, err := k.IncrementModuleAccountNonce(ctx); err != nil {
		return nil, err
	}

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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
