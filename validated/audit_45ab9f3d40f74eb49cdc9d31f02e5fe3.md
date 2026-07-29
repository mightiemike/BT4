No TWAP or time-weighted oracle mechanism exists anywhere in the codebase — confirming the quote path relies solely on `QuoterV2.quoteExactInputSingle`'s instantaneous spot price.

### Title
Inbound GAS auto-swap slippage protection relies on manipulable spot-price quote, enabling value extraction from user deposits - (File: x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
Push Chain's post-audit fix for the "compound"-class bug replaced a caller-supplied `minPCOut`/slippage parameter with a value computed on-chain from `GetSwapQuote` (`QuoterV2.quoteExactInputSingle`) using a fixed 5% band. However, `quoteExactInputSingle` is called with `sqrtPriceLimitX96 = 0` and returns the pool's *instantaneous* spot price rather than a time-weighted average. There is no TWAP or external price cross-check anywhere in the codebase. An unprivileged user who can trade against the same Uniswap V3 pool used by `UniversalCore` can skew the spot price immediately before an inbound GAS/GAS_AND_PAYLOAD auto-swap executes, causing the "protected" `minPCOut` itself to be computed from the manipulated price, defeating the slippage guard and extracting value from the depositor's auto-swapped funds.

### Finding Description
`ExecuteInboundGas` (x/uexecutor/keeper/execute_inbound_gas.go:104-153) and `gasAndPayloadDepositAutoSwap` (x/uexecutor/keeper/execute_inbound_gas_and_payload.go:348-379) both:
1. Call `GetSwapQuote` [1](#0-0) , which invokes `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96: big.NewInt(0)` — an unbounded, spot-price read of the pool's current tick.
2. Immediately derive `minPCOut = quote * 95 / 100` [2](#0-1)  and pass it straight into `CallPRC20DepositAutoSwap` [3](#0-2) , which also passes `deadline = 0` ("contract uses its default") — no execution-time deadline bound either.

The same pattern repeats for the outbound gas-refund path in `applyGasRefund` [4](#0-3) , which also computes `minPCOut` from the same non-TWAP `GetSwapQuote`/`getSwapQuoteForRefund` spot price.

The 5% band was explicitly introduced (per `app/upgrades/chain-meta/upgrade.go:62-67`) as the fix for exactly the class of bug in the external report — replacing a "previous 0-slippage call." But the fix only removes attacker control over the *slippage parameter itself*; it does not address that the *reference price the slippage is measured against* is a manipulable on-chain spot price, identical in structure to the GMX vault price feed flaw described in the source report. Any unprivileged actor can move the Uniswap V3 pool's spot price with an ordinary swap transaction (front-running/sandwiching the block in which the inbound quorum-finalizing vote lands), causing the quote-and-swap sequence — which executes atomically within a single keeper call, with no separate confirmation step — to settle at an attacker-favorable, manipulated rate.

### Impact Explanation
This corrupts native/PRC20 asset accounting: the depositor's bridged GAS funds are auto-swapped into wrapped PC (WPC) at a price the attacker has skewed, so the recipient UEA receives less WPC than fair value while the attacker's counter-trade captures the difference. This falls under "corruption of PRC20 or native asset accounting" and "unauthorized ... release ... of user or protocol-controlled funds" since value is extracted from ordinary user deposit flows with no privileged actor involved — only an unprivileged trader interacting with the same pool used internally by the protocol.

### Likelihood Explanation
Likelihood is bounded by pool liquidity/depth (thin pools are cheaper to manipulate) and by the attacker's ability to get a manipulation transaction ordered adjacent to the inbound-finalizing vote in the same block — achievable via gas-price bidding in an open mempool, not by any validator or relayer collusion. Given inbound execution is deterministic and triggered as soon as quorum is reached, and given the swap parameters (pool, fee tier) are discoverable on-chain, this is a realistic MEV-style attack rather than a purely theoretical one.

### Recommendation
Do not derive `minPCOut` solely from the same block's spot-price quote. Use a TWAP-based quote (e.g., Uniswap V3 `OracleLibrary`/`observe()` over a meaningful window) or cross-check the spot quote against an independent price source before accepting it as the reference for the 5% band. Alternatively, widen the deposit's execution window with a real `deadline` and add circuit-breaker bounds (e.g., reject if spot price deviates more than X% from a longer-window average) so a single-block price shock cannot be laundered into the "protected" minimum.

### Proof of Concept
1. Attacker monitors the mempool/chain for an inbound GAS deposit that is about to reach validator quorum (this is a normal user-triggered cross-chain deposit, e.g. bridging ETH gas funds to Push Chain).
2. Immediately before (or in the same block as) the vote that finalizes the inbound and triggers `ExecuteInboundGas`, the attacker submits a large swap against the PRC20/WPC Uniswap V3 pool referenced by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, pushing the spot price against the pending deposit's swap direction.
3. `GetSwapQuote` reads this manipulated spot price via `quoteExactInputSingle` (no TWAP, `sqrtPriceLimitX96=0`) [5](#0-4) .
4. `minPCOut = quote * 95 / 100` is computed from this bad quote and passed to `depositPRC20WithAutoSwap`, so the swap still "succeeds" against the attacker-favorable minimum [6](#0-5) .
5. The attacker reverses their manipulation trade after the deposit's auto-swap executes, capturing the price difference at the depositor's expense — with no privileged role required at any step.

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-148)
```go
						if execErr == nil {
							// 5% slippage: minPCOut = quote * 95 / 100
							minPCOut := new(big.Int).Mul(quote, big.NewInt(95))
							minPCOut.Div(minPCOut, big.NewInt(100))

							// --- step 5: deposit + swap
							receipt, execErr = k.CallPRC20DepositAutoSwap(sdkCtx, prc20AddressHex, ueaAddr, amount, fee, minPCOut)
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
