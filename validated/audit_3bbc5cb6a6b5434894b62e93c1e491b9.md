## Analog Found: Manipulable Spot-Price Quote Used as the Slippage Reference for PRC20 Auto-Swap Deposits and Gas Refunds

### Title
Hardcoded slippage bound is computed from a manipulable Uniswap V3 spot quote, providing no real MEV/price-manipulation protection on gas-deposit auto-swaps and gas refunds - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`, `x/uexecutor/keeper/evm.go`)

### Summary
The original report flags a flashloan liquidator that computes its `amountOutMin`/reward threshold from a price that can itself be manipulated within the same transaction, so the slippage guard is checked against corrupted data rather than a trustworthy reference. Push Chain's `uexecutor` module reproduces this exact pattern in its gas-abstraction auto-swap path: `GetSwapQuote` fetches a spot-price quote from Uniswap V3's `QuoterV2.quoteExactInputSingle` [1](#0-0) , and that same manipulable quote is used, with a hardcoded 5% haircut, as the `minPCOut` slippage guard passed into the actual on-chain swap execution.

### Finding Description
`ExecuteInboundGas` (for `GAS` inbound routes) computes the swap protection like this: [2](#0-1) 

The same pattern is duplicated for `GAS_AND_PAYLOAD` routes in `gasAndPayloadDepositAutoSwap`: [3](#0-2) 

And again for excess-gas refunds in `applyGasRefund` / `getSwapQuoteForRefund`: [4](#0-3) [5](#0-4) 

In all three call sites, `quote` comes from `GetSwapQuote`, which is a direct, uncached, non-TWAP read of `QuoterV2.quoteExactInputSingle` against the live Uniswap V3 pool state at execution time [1](#0-0) . `minPCOut = quote * 95 / 100` is then handed straight to `CallPRC20DepositAutoSwap`, which performs the real swap via `UniversalCore.depositPRC20WithAutoSwap` against the same pool [6](#0-5) .

Because the "protection" threshold and the actual swap execution price are derived from the identical, attacker-reachable pool state, the 5% band bounds slippage *relative to whatever price currently exists in the pool* — it does not bound the deposited user's outcome relative to a fair/independent price. An unprivileged actor who moves the pool price (e.g., a large permissionless swap against the same PRC20/WPC pair on Push Chain's EVM, executed in a preceding transaction/block or interleaved around validator-triggered inbound finalization) depresses the quote that `GetSwapQuote` returns; `minPCOut` is then computed off that already-depressed number, so the check trivially "passes" while the user's PC output is far below fair value. The fee tier used (`GetDefaultFeeTierForToken`) further concentrates liquidity into a single, more easily moved pool, and there is no TWAP/observation-based reference anywhere in the flow (`timestampObservedAtByChainNamespace` in the ABI relates to chain-meta gas price oracle, not this swap) [7](#0-6) .

This directly parallels the external report's root cause: a swap "protection" value calculated from the same manipulable source the swap itself executes against, instead of independent, user-supplied, or robust price data.

### Impact Explanation
`ExecuteInboundGas` and `ExecuteInboundGasAndPayload` are triggered on every honest, validator-finalized `GAS`/`GAS_AND_PAYLOAD` inbound — i.e., ordinary users bridging native gas assets into Push Chain, which the module upgrade notes confirm is a live, default behavioral path ("GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2 contract ... replacing the previous 0-slippage call") [8](#0-7) . `applyGasRefund` similarly runs on every successful/failed outbound with excess gas, refunding value back to ordinary senders [9](#0-8) . In all cases, a depositing/refunded user can receive materially less PC than the fair-market amount because the only guardrail is derived from the manipulated price itself — this is a permanent, unauthorized loss of user-controlled funds during a default, unprivileged transaction path, matching the in-scope impact of "permanent loss ... of user or protocol-controlled funds" and "corruption of ... gas fee accounting."

### Likelihood Explanation
No privileged access is required: any unprivileged actor able to move the relevant PRC20/WPC Uniswap V3 pool (a normal permissionless EVM interaction on Push Chain) shortly before a target's gas-deposit inbound or gas-refund is processed can degrade the quote used for that target's swap. Because the check bar moves with the manipulation itself, standard capital-based pool manipulation is sufficient — no consensus, validator, or key-compromise assumption is needed, consistent with the "unprivileged external attacker" constraint of the scope.

### Recommendation
Do not derive the slippage floor solely from a live spot quote of the same pool the swap executes against. Use a manipulation-resistant reference (e.g., TWAP over a meaningful window, or a Chainlink/oracle-backed price) to bound acceptable output, and/or allow the depositing party to supply their own maximum-acceptable-slippage parameter rather than a fixed protocol-wide 5%, mirroring the original report's recommendation of user-configurable slippage tolerance.

### Proof of Concept
1. Attacker identifies a PRC20 token/WPC Uniswap V3 pool used by `UniversalCore` for gas-token auto-swaps (`defaultFeeTier[prc20]`).
2. Attacker executes a large swap against that pool (permissionless EVM tx) to depress the PRC20→WPC price immediately before (or interleaved with) a validator-finalized `GAS`/`GAS_AND_PAYLOAD` inbound for a victim is executed by `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`.
3. `GetSwapQuote` returns a depressed `quote` reflecting the manipulated pool state [10](#0-9) .
4. `minPCOut = quote * 95 / 100` is computed from this depressed number [11](#0-10) .
5. `CallPRC20DepositAutoSwap` executes the swap against the same manipulated pool, satisfying `minPCOut` trivially while delivering far less PC to the victim's UEA than fair value [6](#0-5) .
6. Attacker reverses the manipulation (arbitrage back), profiting from the value the victim lost, or simply causes griefing/value loss to victims without needing to profit directly.

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

**File:** x/uexecutor/keeper/outbound.go (L213-223)
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

**File:** x/uexecutor/types/abi.go (L362-368)
```go
    {
      "type": "function",
      "name": "timestampObservedAtByChainNamespace",
      "inputs": [{ "name": "", "type": "string", "internalType": "string" }],
      "outputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
      "stateMutability": "view"
    },
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

**File:** app/upgrades/chain-meta/upgrade.go (L77-84)
```go
		// ── Feature 6 ───────────────────────────────────────────────────────────
		// On a successful outbound observation, if gas_fee_used < gas_fee the
		// excess is refunded to the sender (or fund_recipient) via
		// UniversalCore.refundUnusedGas.  A swap (gasToken → PC native) is
		// attempted first; on failure the raw PRC20 is deposited directly.
		// The result is persisted in OutboundTx.pc_refund_execution.
		// No state migration required.
		logger.Info("Feature: excess gas fee refund executed on successful outbound vote finalisation")
```
