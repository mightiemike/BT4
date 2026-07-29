## Analysis: Sandwich-attack exposure in Push Chain's gas-abstraction auto-swap (spot AMM price used for slippage protection)

The reported bug class (missing/weak slippage protection enabling sandwich/front-running attacks on a swap instruction) maps onto Push Chain's gas-abstraction inbound swap path in `x/uexecutor`. The protocol *does* apply a slippage bound (unlike the literal absence in the report), but the bound is computed from a manipulable spot price rather than a manipulation-resistant oracle, which reproduces the same underlying invariant violation (trade executed at attacker-influenced price) rather than the same code shape.

### Title
Gas-abstraction auto-swap slippage protection relies on manipulable spot AMM price, enabling sandwich extraction from every inbound gas swap - (File: `x/uexecutor/keeper/evm.go`, `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
Every `GAS` / `GAS_AND_PAYLOAD` inbound, and every excess-gas refund on outbound finalization, triggers a protocol-initiated Uniswap V3 swap (PRC20 → WPC) via `CallPRC20DepositAutoSwap` / `CallUniversalCoreRefundUnusedGas`. The minimum acceptable output (`minPCOut`) is derived from `GetSwapQuote`, which calls `QuoterV2.quoteExactInputSingle` — a **spot-price** simulation against the pool's current reserves — and then applies a fixed 5% haircut [1](#0-0) . Because the quote and the swap execution happen sequentially in ordinary block processing with no TWAP or manipulation-resistance check, an unprivileged actor can move the pool price with an ordinary (or flash-loan-funded) trade immediately before the module's swap lands, then reverse it afterward, extracting value up to the full 5% band from the protocol-executed swap on every affected inbound/refund — a classic sandwich attack, matching the report's core failure mode.

### Finding Description
`GetSwapQuote` reads `quoteExactInputSingle` directly off the live pool state via a static `CallEVM` [2](#0-1) . This value is used, unmodified except for a flat 5% discount, as the `minPCOut` bound passed into the state-changing `CallPRC20DepositAutoSwap` call [3](#0-2) .

This pattern is repeated in three call sites:
- `ExecuteInboundGas` (GAS-only inbound swap) [4](#0-3) 
- `gasAndPayloadDepositAutoSwap` (GAS_AND_PAYLOAD inbound swap) [5](#0-4) 
- `applyGasRefund` (outbound excess-gas refund swap back to native PC) [6](#0-5) 

None of these paths reference a time-weighted average price, a maximum quote-age check, or any deviation check against a longer observation window — the quote is taken and consumed within the same keeper call, against whatever the pool's spot reserves are at that exact moment. This exactly mirrors the report's root cause: the acceptable price band is derived from a price an attacker can move at will just before the protected trade executes, so the "slippage check" only bounds the *worst case*, and an attacker can reliably realize that worst case by sandwiching every swap. The upgrade notes confirm this 5% mechanism was added specifically "to fix" a prior 0-slippage state [7](#0-6)  — i.e., the team's own remediation for the identical bug class stopped at a fixed spot-price band rather than a manipulation-resistant reference price.

### Impact Explanation
Every user who bridges a gas token (GAS / GAS_AND_PAYLOAD inbound) or receives an excess-gas refund is exposed. An attacker who observes the pending inbound (or simply trades opportunistically around known high-volume windows) can:
1. Buy WPC out of the pool right before the module's auto-swap executes, pushing the PRC20→WPC price down toward (but not below) the 5%-discounted floor.
2. Let the module's swap execute at this degraded price, receiving `minPCOut` (or just above it) instead of the fair-market amount.
3. Sell back immediately after, capturing the difference.

The victim's UEA is credited with up to 5% less native PC token than the fair-value swap would have produced — a systematic, repeatable value transfer from ordinary users to any actor capable of adjacent-block sequencing (which does not require validator or relayer privilege). Because this executes automatically on every affected inbound/refund with no cap on pool depth or trade frequency, the aggregate drain scales with protocol volume — this is a "corruption of ... gas fee accounting / refund accounting" reachable purely from ordinary user deposits, per the allowed-impact gate.

### Likelihood Explanation
High. No privileged role is required — sandwiching a DEX quote used by predictable, publicly observable protocol logic is a well-understood and automatable MEV technique, and the trigger condition (any gas-abstraction inbound or gas refund) is a routine, frequent user action rather than an edge case. The fixed 5% band is generous enough that a lightly-liquid pool is trivially movable by that margin with a single trade.

### Recommendation
Replace or supplement the spot `quoteExactInputSingle` reference price with a manipulation-resistant source before computing `minPCOut`:
- Use a TWAP (time-weighted average price) over a meaningful window from the same Uniswap V3 pool (`observe`/`increaseObservationCardinalityNext`), or
- Cross-check the spot quote against an external chain-meta-style validator-attested price and reject/clamp swaps whose spot quote deviates beyond a tight tolerance from that reference, or
- Reduce the fixed slippage tolerance to reflect actual expected pool depth per token, and add a maximum quote age / same-block manipulation guard.

Apply this uniformly to `GetSwapQuote`'s three call sites (`ExecuteInboundGas`, `gasAndPayloadDepositAutoSwap`, `applyGasRefund`).

### Proof of Concept
1. Identify the PRC20/WPC Uniswap V3 pool backing a supported gas token (`GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`).
2. Submit an inbound `GAS` transaction (or trigger an outbound with excess gas fee) for a modest-liquidity pool.
3. In the same or adjacent block, submit a large buy of WPC against that pool to depress the PRC20→WPC exchange rate by close to 5%.
4. Observe `ExecuteInboundGas` → `GetSwapQuote` → `minPCOut = quote*95/100` compute a materially worse `minPCOut` than the pre-attack fair price, and `CallPRC20DepositAutoSwap` execute successfully at that degraded rate [8](#0-7) .
5. Reverse the initial trade to realize the captured spread; repeat per inbound/refund.

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
