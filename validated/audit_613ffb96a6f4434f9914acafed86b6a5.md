I have sufficient evidence to finalize the analysis. The core finding: `GetSwapQuote` (`x/uexecutor/keeper/evm.go:502-538`) reads the **instantaneous spot price** from the on-chain Uniswap V3 pool via `QuoterV2.quoteExactInputSingle`, and this same-transaction quote is then used to derive `minPCOut` (95% of quote) for the actual swap executed moments later in `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas`. Since both the quote and the swap read/act on the same manipulable pool state, an attacker can sandwich the entire quote+swap sequence by moving the pool price beforehand — the "slippage protection" is computed from the very state the attacker just poisoned, so it constrains nothing.

### Title
Same-block spot-price quote used as slippage bound allows sandwich attacks on inbound gas-abstraction and gas-refund swaps - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The `x/uexecutor` module performs on-chain PRC20→WPC swaps against a Uniswap V3 pool for gas-abstraction (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`) and for excess-gas refunds (`applyGasRefund`). To bound slippage, it calls `GetSwapQuote` immediately before the swap and sets `minPCOut = quote * 95 / 100`. Because the quote is read from the same publicly-tradable Uniswap V3 pool used for the swap, and nothing prevents an attacker from moving that pool's price with an ordinary swap transaction immediately before the module's quote+swap sequence lands in the same block, the "protection" is derived from attacker-manipulated state rather than a manipulation-resistant reference price (e.g., TWAP).

### Finding Description
`GetSwapQuote` ( [1](#0-0) ) calls `quoteExactInputSingle` on the configured `QuoterV2` contract, which returns the amount out based on the pool's current instantaneous state. The 95%-of-quote `minPCOut` is then passed straight into `CallPRC20DepositAutoSwap` ( [2](#0-1) ), which performs the real swap via `depositPRC20WithAutoSwap` on `UniversalCore`.

This quote→swap sequence is invoked synchronously within a single keeper call, both in the inbound `GAS` path ( [3](#0-2) ) and the `GAS_AND_PAYLOAD` path ( [4](#0-3) ), which run as part of processing a validator's `MsgVoteInbound` once quorum is reached. The identical pattern is used for gas refunds in `applyGasRefund` ( [5](#0-4) ), triggered from ordinary `MsgVoteOutbound` finalization.

Because the pool is a standard Uniswap V3 pool deployed on Push Chain's own EVM and reachable by any address via ordinary swap transactions, an unprivileged attacker who observes a pending vote transaction that will trigger one of these swaps (the vote itself, or the underlying inbound/outbound event, is publicly visible before finalization) can:
1. Front-run with a large swap on the PRC20/WPC pool to push the price against the direction the module will trade.
2. Let the module's vote-triggered swap execute — `GetSwapQuote` returns the manipulated price, `minPCOut` is set to 95% of that already-bad price, and the swap clears the check while receiving far less WPC than fair value.
3. Back-run to unwind the attacker's position and capture the extracted value.

This is the same failure mode as the seed report's 0-slippage `UniswapHandler` calls: the code now sets a nonzero `minPCOut`, but the reference price it is derived from is itself the manipulable spot price of the exact pool being traded against in the same transaction sequence, so it does not defend against sandwiching — only against unrelated multi-block staleness. The upgrade note in [6](#0-5)  confirms this was the intended fix for the original 0-slippage issue, but the fix does not use a manipulation-resistant price source (e.g., TWAP/oracle).

### Impact Explanation
Every `GAS` and `GAS_AND_PAYLOAD` inbound (funds bridged by ordinary external-chain users) and every outbound gas refund is exposed to this sandwich, directly reducing the amount of native `PC`/`WPC` credited to the user's UEA or refund recipient versus fair market value, with the difference extracted by the attacker from the pool. Since the swap source (module-held PRC20) ultimately originates from bridged user/protocol funds, this constitutes unauthorized value extraction/draining of user funds through a user-reachable execution path, matching "corruption of PRC20 or native asset accounting" and "unauthorized... state transitions in universal execution flows" in the allowed impact list.

### Likelihood Explanation
Every ordinary inbound `GAS`/`GAS_AND_PAYLOAD` transaction and every outbound observation with excess gas triggers this code path — no privileged or malicious-validator behavior is required, only an attacker capable of submitting ordinary swap transactions on the pool and observing pending votes/inbound events (both are public). The severity of extraction scales with pool liquidity depth and the size of the bridged amount, similar to any standard AMM sandwich.

### Recommendation
Do not derive `minPCOut` solely from the same pool's instantaneous `quoteExactInputSingle` result taken immediately before the swap. Instead, base slippage protection on a manipulation-resistant reference (e.g., a TWAP over a sufficient window from the same pool, or an external price oracle), and/or bound the acceptable deviation between the TWAP-based reference and spot price before allowing the swap to proceed, aborting/falling back to the no-swap raw PRC20 deposit path if the deviation is excessive.

### Proof of Concept
1. Attacker monitors the mempool/inbound events for a pending `GAS` or `GAS_AND_PAYLOAD` inbound with a large `amount` (or an outbound observation implying a large excess-gas refund).
2. Attacker submits a swap on the PRC20/WPC Uniswap V3 pool that pushes the price down in the direction the module will later sell PRC20 for WPC.
3. Validators reach quorum on the pending vote; `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`/`applyGasRefund` executes `GetSwapQuote` against the now-manipulated pool and computes `minPCOut = quote*95/100`, which is trivially satisfied by the manipulated pool.
4. `CallPRC20DepositAutoSwap`/`CallUniversalCoreRefundUnusedGas` executes the swap at the poor price, crediting the user's UEA/refund recipient with less WPC than fair value.
5. Attacker reverses their position (sells the PRC20/buys back WPC) in a back-run trade, capturing the value difference extracted from the module's swap.

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

**File:** x/uexecutor/keeper/evm.go (L540-592)
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
