This confirms `ExecuteInboundGas` runs synchronously as part of finalizing `MsgVoteInbound` processing — it fetches the Uniswap V3 quote and immediately swaps in the same execution, using a spot-price-derived slippage bound rather than a value fixed by any external actor. That's the exact analog of the ToB pattern: a "slippage guard" that is computed from state an unprivileged actor can manipulate immediately beforehand.

### Title
Gas-abstraction auto-swap slippage guard is computed from a manipulable spot price, enabling sandwich theft of inbound gas top-ups - (File: x/uexecutor/keeper/execute_inbound_gas.go)

### Summary
`ExecuteInboundGas` (and the equivalent path in `ExecuteInboundGasAndPayload`) executes a Uniswap V3 swap on behalf of the inbound sender to convert a deposited PRC20 into WPC/PC as a gas top-up. The "slippage guard" (`minPCOut`) is derived from a `QuoterV2.quoteExactInputSingle` call fetched at the moment of execution, then multiplied by 95%. Because that reference quote is read from the live, on-chain Uniswap pool state rather than a value supplied or bounded independently by the user, an unprivileged actor can manipulate the pool price immediately before the finalizing vote executes, causing the "protected" swap to execute against an already-degraded price. This mirrors the Trail of Bits `Pools.sol` finding: a slippage guard that is only checked against a reference ratio which itself has just been reset/manipulated by the attacker provides no real protection.

### Finding Description
The flow is:
1. `MsgVoteInbound` reaches quorum (visible in the mempool since these are ordinary, gasless, publicly broadcast UV votes) — see `x/uexecutor/keeper/voting.go` finalization path.
2. On finalization, `ExecuteInboundGas` runs synchronously in the same block: [1](#0-0) 
3. `GetSwapQuote` performs a *static* call to `QuoterV2.quoteExactInputSingle` against the live pool reserves at that block: [2](#0-1) 
4. `minPCOut` is derived purely from that just-fetched quote (`quote * 95 / 100`), then immediately used to bound the real swap executed via `CallPRC20DepositAutoSwap`: [3](#0-2) [4](#0-3) 

The same pattern repeats for `gasAndPayloadDepositAutoSwap` [5](#0-4)  and for outbound gas refunds in `applyGasRefund`/`getSwapQuoteForRefund` [6](#0-5) .

Because the reference price and the executed swap are computed back-to-back inside the *same* module-driven transaction, the 5% band only protects against price movement *during* that single transaction — it does nothing against price movement caused *before* it. An unprivileged actor watching the mempool for the finalizing `MsgVoteInbound`/`MsgVoteChainMeta` transaction can, in the same or a preceding block, submit an ordinary swap against the PRC20/WPC Uniswap V3 pool to push the price down, so that `quoteExactInputSingle` itself returns a already-degraded `amountOut`. The subsequent `minPCOut = quote * 0.95` check is trivially satisfied by the manipulated execution, and the attacker then reverses their trade to capture the difference — a classic sandwich. The upgrade history confirms this is an evolving-but-still-incomplete mitigation: an earlier version had *no* slippage protection at all (0-slippage), and the "fix" changed it to a 5% band computed the same insecure way. [7](#0-6) 

This is the same root-cause invariant break as the ToB report: a protective bound (slippage guard / reserve-ratio check) that is derived from state the attacker fully controls immediately prior to the protected action, rather than from a value fixed independently of the attacker's actions.

### Impact Explanation
This directly corrupts gas-fee/PRC20-swap accounting for ordinary inbound users: the amount of PC/WPC actually credited to the recipient's UEA for gas top-up (and the outbound gas refund path) can be meaningfully less than fair value, with the difference captured by the sandwiching party — an unauthorized transfer of protocol/user-controlled value reachable purely from unprivileged, ordinary swap transactions against the pool plus observation of public inbound-vote traffic. This falls within the "corruption of ... gas fee accounting, refund accounting ... token mapping" and "stealing ... funds" allowed-impact categories, triggered solely by unprivileged user action (no malicious validator, TSS participant, or admin required).

### Likelihood Explanation
Requires only: (1) visibility of pending `MsgVoteInbound`/`MsgVoteChainMeta` transactions in the mempool (these are ordinary gasless broadcast transactions, not privileged), and (2) the ability to submit an ordinary Uniswap V3 swap transaction against the relevant PRC20/WPC pool, both of which are unprivileged actions available to any user. Effectiveness scales inversely with pool liquidity/depth for a given PRC20, which is realistic for newly listed or thin PRC20 pairs.

### Recommendation
- Short term: Do not derive `minPCOut` solely from a spot quote fetched in the same execution as the swap. Use a manipulation-resistant reference (e.g., a TWAP over multiple blocks, or a governance/registry-configured maximum acceptable slippage relative to an independently tracked reference price) and/or bound the deviation between the on-chain quote and a recent moving-average price before accepting it as the basis for `minPCOut`.
- Long term: Treat any "slippage guard" whose reference value is computed from the exact same manipulable state as the protected trade as providing no real protection — audit all `GetSwapQuote` → `minPCOut` → swap-execution sequences (inbound gas, gas+payload, and outbound refund paths) under this lens, and consider moving swap execution off the automatic finalization hot-path (e.g., allow relayer/keeper-supplied `minPCOut` with a hard registry-configured floor, or defer/aggregate swaps to reduce predictability/timeability by MEV actors).

### Proof of Concept
1. Attacker monitors the P2P mempool for gasless `MsgVoteInbound` transactions targeting a `GAS`/`GAS_AND_PAYLOAD` inbound for a specific low-liquidity PRC20 (source-chain deposit is public/observable independently, e.g. on the source chain itself, before the UV even votes).
2. Attacker submits an ordinary swap (sell PRC20 for WPC) against the target Uniswap V3 pool in a preceding or same block, depressing the PRC20→WPC price.
3. The finalizing vote lands; `ExecuteInboundGas` calls `GetSwapQuote` and computes `minPCOut = quote * 0.95` against the now-depressed price. [8](#0-7) 
4. `CallPRC20DepositAutoSwap` executes the real swap at the depressed price, crediting the recipient's UEA with less WPC/PC than fair value would have provided. [9](#0-8) 
5. Attacker reverses their initial trade (buy back PRC20 with WPC), realizing profit equal to the value lost by the inbound recipient, bounded only by pool depth and available capital — not by the nominal "5% slippage" figure, since that figure was computed against an already-manipulated baseline.

### Citations

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L103-140)
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L142-153)
```go
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

**File:** app/upgrades/chain-meta/upgrade.go (L62-67)
```go
		// ── Feature 4 ───────────────────────────────────────────────────────────
		// GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2
		// contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%)
		// to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call.
		// No state migration required.
		logger.Info("Feature: Uniswap V3 QuoterV2 used for minPCOut (5% slippage) on GAS / GAS_AND_PAYLOAD routes")
```
