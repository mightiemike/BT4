## Title
Spot-price Uniswap V3 quote used for module-driven `depositPRC20WithAutoSwap` slippage protection enables price-manipulation extraction of bridged user funds — (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
The external report's bug class is: a protocol computes a reward/exchange amount from mutable pool/position state that the very same user can move (via flashloan-scale capital) immediately before triggering the state-dependent payout, then reverse it — extracting value that should have gone to other participants/the protocol. The closest reachable analog in this repository is the `GAS` / `GAS_AND_PAYLOAD` inbound execution path and the outbound gas-refund path, both of which fetch an instantaneous Uniswap V3 `QuoterV2.quoteExactInputSingle` spot quote and derive `minPCOut` as `quote * 95 / 100`, then immediately execute `depositPRC20WithAutoSwap` against that same pool.

### Finding Description
`ExecuteInboundGas` [1](#0-0)  and `gasAndPayloadDepositAutoSwap` [2](#0-1)  both call `k.GetSwapQuote` to read the current spot price from the pool, compute `minPCOut = quote * 95 / 100` (a flat 5% slippage tolerance), and then immediately call `k.CallPRC20DepositAutoSwap`, which performs the real swap on the same pool via `depositPRC20WithAutoSwap` [3](#0-2)  The quote comes straight from `QuoterV2.quoteExactInputSingle`, an instantaneous/spot price read with no TWAP or oracle-deviation check [4](#0-3) .

The same pattern also exists on the outbound gas-refund path (`applyGasRefund` / `getSwapQuoteForRefund` / `CallUniversalCoreRefundUnusedGas`) [5](#0-4) .

Because `quote` and the resulting swap both execute against the *live* pool price in the same block, and the pool itself (a standard Uniswap V3 pool, `UniversalCore`'s configured quoter/factory) is externally tradable by anyone, an unprivileged actor can move the pool price with their own capital before the inbound finalizes, causing the module-controlled swap of the user's own (or another user's) bridged PRC20 into WPC to execute at a manipulated, disadvantageous price — while still satisfying the 5%-slippage floor because that floor was computed from the same manipulated price a moment earlier. The attacker then reverses their trade in the same or next block, capturing the value that should have accrued to the depositor/protocol as arbitrage profit. This mirrors the `liquidity_lockbox` bug class: a state-dependent payout (LP reward / swap output) is computed from data the caller can cheaply and atomically manipulate around the payout event.

### Impact Explanation
This corrupts the amount of PRC20/WPC actually credited to the UEA/recipient during `GAS` and `GAS_AND_PAYLOAD` inbound execution, and the amount refunded during outbound gas-refund — a direct loss of user/protocol-controlled funds via unfavorable, attacker-influenced swap execution, falling under "corruption of PRC20 or native asset accounting" and "unauthorized… state transitions in universal execution flows" in the allowed-impact gate.

### Likelihood Explanation
Reachable by an ordinary unprivileged user: any address can trade against the same Uniswap V3 pool used by `UNIVERSAL_CORE`/`QuoterV2`, and inbound execution (hence the quote-then-swap sequence) is triggered deterministically once honest Universal Validators finalize a `MsgVoteInbound`/`MsgVoteOutbound` — an event visible in the mempool/consensus process, giving an attacker a predictable window to front-run/back-run. No privileged access, malicious validator, or protocol bug beyond the spot-price dependency is required. Actual profitability depends on the target pool's liquidity depth, which is likely to be thin for smaller PRC20/WPC pairs.

### Recommendation
Replace or augment the spot `quoteExactInputSingle` read with a TWAP-based quote (or an external price oracle) for computing `minPCOut`, and/or clamp the acceptable spot-vs-TWAP deviation before permitting the swap. Consider widening the atomicity assumption check by re-validating pool price staleness/deviation immediately before `CallPRC20DepositAutoSwap` executes, and evaluate whether critical bridging amounts should bypass auto-swap in favor of a fixed-rate or oracle-priced path when pool liquidity is below a safety threshold.

### Proof of Concept
1. Attacker identifies a pending `MsgVoteInbound` (or `MsgVoteOutbound`) about to reach quorum for a `GAS`/`GAS_AND_PAYLOAD` inbound (or a successful outbound triggering a gas refund) that will route a meaningful PRC20 amount through `depositPRC20WithAutoSwap`.
2. Attacker submits a large swap against the same Uniswap V3 pool (prc20 → wpc or vice versa) to move the spot price, timed to land in the same block just before the finalizing validator vote transaction.
3. The finalizing vote transaction executes `GetSwapQuote` [6](#0-5)  against the now-manipulated pool, computes `minPCOut` from that skewed quote, and calls `CallPRC20DepositAutoSwap` [7](#0-6) , which swaps the bridged/refunded amount at the manipulated price.
4. Attacker reverses their initial trade, restoring the pool to fair price and capturing the spread as profit — funded by the value that should have gone to the deposit recipient (or the refund recipient).

### Citations

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

**File:** x/uexecutor/keeper/evm.go (L500-593)
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

**File:** x/uexecutor/keeper/outbound.go (L213-270)
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
