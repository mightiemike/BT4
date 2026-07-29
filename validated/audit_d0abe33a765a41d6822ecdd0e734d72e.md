## Analysis

The Predy `Perp.reallocate` bug is rooted in an unprivileged, predictably-triggerable state transition that consumes a **manipulable spot price with no TWAP protection**, letting an attacker sandwich the transition and extract value.

Push Chain's `uexecutor` module has a directly analogous pattern in its GAS/GAS_AND_PAYLOAD inbound-execution and gas-refund flows: `GetSwapQuote` calls the on-chain Uniswap V3 `QuoterV2.quoteExactInputSingle` (a spot-price read of the live pool, with `SqrtPriceLimitX96` hard-coded to `0`, i.e., no price limit) and the result is used to compute a fixed `minPCOut = quote * 95/100` slippage bound that is passed straight into a real swap executed by the protocol module account. [1](#0-0) [2](#0-1) [3](#0-2) 

The upgrade notes explicitly confirm this design: `"GAS and GAS_AND_PAYLOAD inbound routes now call the Uniswap V3 QuoterV2 contract to obtain an on-chain swap quote and pass minPCOut (quote × 95%) to CallPRC20DepositAutoSwap, replacing the previous 0-slippage call."` [4](#0-3) 

The same quote-then-swap pattern with a fixed 5% band is reused in the gas-refund path (`getSwapQuoteForRefund` → `CallUniversalCoreRefundUnusedGas`), meaning excess gas fees returned to a user/recipient are also priced off the same manipulable spot quote. [5](#0-4) 

### Why this matches the H-01 root cause
- Both `quoteExactInputSingle` (spot price at query time) and the subsequent `depositPRC20WithAutoSwap` (actual swap) execute in the **same EVM call context inside `ExecuteInboundGas`/`ExecuteInboundGasAndPayload`**, which itself is triggered deterministically once a quorum of validators' `MsgVoteInbound` votes finalize an inbound — an event that is publicly observable and cannot be delayed by an attacker but *can* be preceded by attacker-controlled transactions in the same or adjacent block, exactly as the Predy `reallocate` trigger is permissionless and externally forceable.
- There is no TWAP, no minimum-liquidity check, and no cross-check against an independent price feed — only a flat ±5% band derived from the instantaneous quote, so any pool-price movement within that band (trivial to induce via a large swap on the same Uniswap V3 pool immediately before the module's forced swap lands) is silently absorbed as "acceptable slippage."
- The swap is **guaranteed and non-cancelable** once vote quorum is reached — the module doesn't re-check price staleness or abort if the quote looks abnormal, so an attacker can reliably sandwich a known-size, known-direction trade (buy/sell PRC20↔WPC in the direction that maximizes their profit against the module's minPCOut floor), draining value from the depositing user (who receives worse PC amount) or from the protocol (in the refund case, where excess PC could be paid out based on a manipulated quote).

### Impact
Unlike the H-01 finding (loss of LP yield), here the concrete corrupted value is the recipient's **actual PC amount credited via `CallPRC20DepositAutoSwap`** (`GAS`/`GAS_AND_PAYLOAD` inbound flows) or the **refunded PC amount via `refundUnusedGas`** — both computed from a spot quote that a permissionless attacker can move within the tolerated 5% band right before the guaranteed swap executes, extracting value from ordinary users' gas deposits/refunds with no privileged access required.

### Title
Spot-price Uniswap V3 QuoterV2 quote used for auto-swap `minPCOut` slippage bound enables sandwich extraction on gas deposit/refund - (File: x/uexecutor/keeper/evm.go, x/uexecutor/keeper/execute_inbound_gas.go, x/uexecutor/keeper/outbound.go)

### Summary
`GetSwapQuote` reads the instantaneous output of `QuoterV2.quoteExactInputSingle` from the live on-chain Uniswap V3 pool with no price-limit and no TWAP, and this spot quote directly derives the `minPCOut` slippage floor (`quote * 95/100`) used for the module's guaranteed, non-cancelable auto-swap in `CallPRC20DepositAutoSwap` (GAS/GAS_AND_PAYLOAD inbound execution) and `CallUniversalCoreRefundUnusedGas` (excess gas refund). [1](#0-0) [2](#0-1) 

### Finding Description
When an inbound of type `GAS` or `GAS_AND_PAYLOAD` reaches vote quorum (an event any observer can predict since inbound votes are on-chain and public), `ExecuteInboundGas`/`ExecuteInboundGasAndPayload` fetches a live quote via `GetSwapQuote` and immediately performs a real swap via `CallPRC20DepositAutoSwap` bounded only by a flat 5% slippage derived from that same spot quote. [2](#0-1)  The identical pattern secures the gas refund path in `getSwapQuoteForRefund`/`applyGasRefund`. [3](#0-2)  Because `SqrtPriceLimitX96` is hard-coded to `0` in the quote call, there is no protection against a moved pool price, and because the swap is unconditionally executed by the module once triggered (no ability for the user to cancel/adjust once the vote lands), an attacker who observes the pending quorum-reaching vote can push the pool price in their favor immediately beforehand (and reverse it after), letting the module's forced swap fill against the manipulated price and pocketing the difference within the 5% tolerance band, repeatable per inbound.

### Impact Explanation
This directly reduces the PC amount credited to users on gas deposits (`GAS`/`GAS_AND_PAYLOAD`) and can inflate PC paid out from `refundUnusedGas`, i.e., unauthorized value extraction from ordinary user-controlled/protocol-controlled funds through corrupted gas/refund accounting — matching the in-scope "corruption of ... gas fee accounting, refund accounting" and "unauthorized ... release ... of user or protocol-controlled funds" impacts, triggerable by an unprivileged external actor with only the ability to submit ordinary swap transactions on the pool used by `UniversalCore`.

### Likelihood Explanation
The trigger requires no privileged role — any address can submit ordinary Uniswap V3 swaps against the pool that `UniversalCoreQuoterAddress`/`WPC` resolve to just before a predictable, publicly observable vote-quorum event executes the module's guaranteed swap. The fixed 5% band is generous compared to normal price impact for reasonably sized gas-deposit swaps, and repeatable across every future GAS/GAS_AND_PAYLOAD inbound and refund event, making this a persistent value-extraction vector rather than a one-off exploit.

### Recommendation
Replace the single spot `quoteExactInputSingle` read with a TWAP-based or multi-sample price check (e.g., compare against a longer observation window, or require the spot quote to be within a bound of a recent moving-average price) before computing `minPCOut`, and/or reduce the slippage tolerance and add a sanity check that aborts/reverts the swap (falling back to a non-swap raw PRC20 deposit) if the spot price deviates significantly from a recent reference price, rather than silently proceeding with a wide fixed percentage band derived purely from the instantaneous quote.

### Proof of Concept
1. Attacker monitors `MsgVoteInbound` submissions for a `GAS`/`GAS_AND_PAYLOAD` inbound approaching validator quorum (public, observable state in `x/uexecutor`).
2. Just before/at the block where the quorum-triggering vote will be processed, attacker submits a large swap on the PRC20↔WPC Uniswap V3 pool referenced by `UniversalCore` to move the spot price unfavorably for the upcoming module swap.
3. When quorum is reached, `ExecuteInboundGas` calls `GetSwapQuote` (spot, no TWAP) and computes `minPCOut = quote*95/100`, then unconditionally executes `CallPRC20DepositAutoSwap` at the manipulated price. [6](#0-5) 
4. Attacker reverses their swap afterward, having captured the spread within the 5% tolerance at the expense of the deposit recipient (or, in the refund path, at the expense of the protocol's `refundUnusedGas` payout). [3](#0-2)

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

**File:** x/uexecutor/keeper/outbound.go (L257-270)
```go
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
