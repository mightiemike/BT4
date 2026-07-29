### Title
Circular slippage protection on Uniswap V3 auto-swap enables sandwich attacks on inbound gas-abstraction deposits - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/execute_inbound_gas_and_payload.go, x/uexecutor/keeper/outbound.go)

### Summary
`x/uexecutor` derives its swap slippage bound (`minPCOut`) from `GetSwapQuote`, a read-only call into UniswapV3 `QuoterV2.quoteExactInputSingle`, and then immediately executes the real swap via `depositPRC20WithAutoSwap`/`refundUnusedGas` against the *same* pool reserves in the same keeper call chain. This is the identical "circular slippage" pattern the external Sablier/Curve report describes: the quote and the exchange both read the pool's current, attacker-manipulable spot state, so the 5% tolerance only protects against price movement between two calls that are guaranteed to see the same state — i.e., it protects against nothing.

### Finding Description
Three independent code paths repeat the exact same anti-pattern:

1. `ExecuteInboundGas` (gas-abstraction inbound path): [1](#0-0) 
computes `quote` via `GetSwapQuote` and then `minPCOut = quote*95/100` before calling `CallPRC20DepositAutoSwap`.

2. `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound path): [2](#0-1) 

3. `applyGasRefund` (outbound gas refund path): [3](#0-2) 

All three rely on: [4](#0-3) 
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `commit=false`, which — like Curve's `get_dy` — reads the pool's *current* reserves/sqrtPrice at call time. Immediately afterward, the same keeper flow calls `CallPRC20DepositAutoSwap`: [5](#0-4) 
which triggers the real `depositPRC20WithAutoSwap` execution on the UniversalCore contract, swapping PRC20 → WPC on the very same pool.

Because the quote and the swap both read the identical on-chain pool state within the same processing step (no external price oracle, no TWAP, `sqrtPriceLimitX96 = 0` meaning unlimited price impact), the derived `minPCOut = quote * 95%` only bounds movement *between* the quote call and the swap call — movement that is architecturally zero since nothing else can execute between them. It provides no protection against the pool having already been skewed by an attacker prior to the whole call.

`ExecuteInbound`/`ExecuteInboundGas` execution is driven by ballot finalization (2/3 UV majority) inside `x/uexecutor` vote processing: [6](#0-5) 
This occurs deterministically once threshold is reached within ordinary transaction/block processing — reachable purely by ordinary user activity (an inbound deposit plus honest validators voting) with no privileged action required. An unprivileged attacker can:
1. Predict/observe that a large inbound gas-abstraction deposit is about to reach finalization (mempool/vote observation is public).
2. Submit an ordinary swap transaction against the same UniswapV3 PRC20/WPC pool in an earlier position of the same block (or the immediately preceding block) to depress the PRC20→WPC price.
3. Let the finalizing vote execute `ExecuteInboundGas`, which reads the now-depressed quote, derives an even-lower `minPCOut`, and completes the auto-swap at the manipulated rate — the check passes because it was computed from the same manipulated reserves.
4. Reverse the manipulation afterward and capture the arbitrage/spread that the victim's deposit lost.

This matches the target pattern precisely: `get_dy`→depressed quote is Push Chain's `quoteExactInputSingle`→depressed quote; `exchange`→real swap is `depositPRC20WithAutoSwap`/`refundUnusedGas`.

### Impact Explanation
Every gas-abstraction inbound deposit (`GAS`, `GAS_AND_PAYLOAD`) and every outbound gas refund passes user or protocol funds through this unprotected auto-swap. A sandwiching attacker can permanently siphon up to ~5% (the `MAX`-style tolerance baked into the `*95/100` calculation) of the swapped value on every such inbound/outbound processed, without needing validator, TSS, or admin privileges — only capital to move the Uniswap V3 pool and the ability to submit an ordinary swap transaction. This is a repeatable, protocol-wide value leak on user-controlled and module-controlled PRC20/WPC conversions (fee abstraction deposits and gas refunds), directly corrupting the PRC20/gas accounting and causing unauthorized value transfer away from depositors/refund recipients.

### Likelihood Explanation
Medium-High. No special access is required — only capital to move the target pool and awareness of pending inbound finalization or refund processing (both are observable on-chain/in mempool). The attack is economically bounded by pool liquidity and the ~5% slippage tolerance, but is repeatable against every affected inbound/outbound, similar to the original report's "attacker can monitor and sandwich multiple vault settlements" scenario.

### Recommendation
Replace the on-chain spot quote (`quoteExactInputSingle`) used for `minPCOut` derivation with a manipulation-resistant reference price — e.g., a TWAP over a sufficiently long window, an external oracle feed for the PRC20/WPC pair, or a protocol-configured fair-price bound — so that `minPCOut` is not computed from the same block-manipulable reserves that the swap itself consumes. Alternatively, restrict `sqrtPriceLimitX96` to a bound derived from a trusted reference price rather than `0` (unlimited), and/or split the quote and swap execution across a delay (e.g., a commit-reveal or multi-block window) so an attacker cannot atomically sandwich both.

### Proof of Concept
Deterministic PoC requires local devnet with a live Uniswap V3 PRC20/WPC pool and controllable liquidity, which is outside available tooling in this analysis (no code execution/filesystem access). Conceptually:
1. Deploy/observe the PRC20/WPC Uniswap V3 pool used by `UniversalCore` for a given `prc20Address`.
2. Attacker submits a large swap into that pool to depress the PRC20→WPC price.
3. In the same block, trigger/observe a pending `MsgVoteInbound` reaching 2/3 threshold for a `GAS` or `GAS_AND_PAYLOAD` inbound tied to that `prc20Address`, causing `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap` to call `GetSwapQuote` (depressed) → `minPCOut = quote*95/100` (depressed) → `CallPRC20DepositAutoSwap` (executes at depressed rate, passes check).
4. Attacker reverses the initial swap, recovering the price and capturing the spread, while the depositor's UEA receives less WPC than fair value.

Because I could not execute Go tests or spin up a devnet with a live Uniswap V3 pool in this environment, this PoC is described at the transaction-flow level rather than as a runnable test; a background engineering session with repo/test execution access would be needed to build the concrete `forge`/Go integration test analogous to the one in the external report.

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

**File:** x/uexecutor/keeper/execute_inbound.go (L10-35)
```go
func (k Keeper) ExecuteInbound(ctx context.Context, utx types.UniversalTx) error {
	k.Logger().Info("execute inbound dispatched",
		"utx_key", utx.Id,
		"tx_type", utx.InboundTx.TxType.String(),
		"source_chain", utx.InboundTx.SourceChain,
		"amount", utx.InboundTx.Amount,
	)

	switch utx.InboundTx.TxType {
	case types.TxType_GAS: // fee abstraction
		return k.ExecuteInboundGas(ctx, *utx.InboundTx)

	case types.TxType_FUNDS: // synthetic
		return k.ExecuteInboundFunds(ctx, utx)

	case types.TxType_FUNDS_AND_PAYLOAD: // synthetic + payload
		return k.ExecuteInboundFundsAndPayload(ctx, utx)

	case types.TxType_GAS_AND_PAYLOAD: // fee abstraction + payload
		return k.ExecuteInboundGasAndPayload(ctx, utx)

	default:
		k.Logger().Error("unsupported inbound tx type", "utx_key", utx.Id, "tx_type", utx.InboundTx.TxType)
		return fmt.Errorf("unsupported inbound tx type: %d", utx.InboundTx.TxType)
	}
}
```
