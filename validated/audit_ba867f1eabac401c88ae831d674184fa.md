This confirms the mechanism: `VoteInbound` executes `ExecuteInbound` (→ `ExecuteInboundGas`/`gasAndPayloadDepositAutoSwap`) synchronously inside the `MsgVoteInbound` handler, at the moment the third (quorum) validator's vote transaction lands in a block [1](#0-0) . This is a valid native analog to the Fei `BondingCurve` issue: a value-conversion mechanism with only an instantaneous slippage bound, publicly triggerable/observable, that an unprivileged party can exploit for extraction.

### Title
Sandwichable spot-price autoswap in inbound gas/PRC20 conversion allows value extraction from user deposits - (File: `x/uexecutor/keeper/execute_inbound_gas.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/evm.go`)

### Summary
Whenever an inbound `GAS` or `GAS_AND_PAYLOAD` deposit is finalized by validator quorum, the `uexecutor` module immediately fetches an on-chain AMM spot quote and executes a swap of the deposited PRC20 into PC (native gas token) using only a fixed 5% slippage buffer computed from that same spot quote, with no TWAP or price-impact protection [2](#0-1) [3](#0-2) . Because the quote-then-swap sequence happens atomically inside the `MsgVoteInbound` message handler that finalizes the ballot [4](#0-3) , an unprivileged attacker who observes the pending (quorum-reaching) vote transaction in the mempool can sandwich it: submit a large swap against the same Uniswap-V3-style pool immediately before it to move the spot price, let the protocol's autoswap execute at the manipulated price (within the 5% tolerance computed from that already-manipulated price), then reverse the trade afterward to capture the difference.

### Finding Description
`GetSwapQuote` calls `QuoterV2.quoteExactInputSingle` with `SqrtPriceLimitX96 = 0`, i.e., an unconstrained, instantaneous pool-price query [5](#0-4) . The resulting `quote` is immediately used to compute `minPCOut = quote * 95/100` and passed into `CallPRC20DepositAutoSwap`, which triggers `depositPRC20WithAutoSwap` on the `UNIVERSAL_CORE` handler contract [6](#0-5) . There is no TWAP oracle, no minimum-liquidity check, and no protection against the quote itself having been manipulated moments earlier in the same block. The entire quote→swap sequence executes deterministically as part of processing the vote transaction that finalizes the inbound ballot — the very transaction that a searcher can see land in the mempool and front-run/back-run around, exactly as bots would race to arbitrage a newly-priced Fei `BondingCurve` purchase. This same pattern is duplicated for `GAS_AND_PAYLOAD` deposits and for `refundUnusedGas` withSwap flows [7](#0-6) .

### Impact Explanation
A successful sandwich reduces the amount of PC actually credited to the user's UEA (still within the nominal 5% band relative to the manipulated price, but far below fair market value), directly diminishing user/protocol-controlled value and enriching the attacker at the expense of the deposit being converted — matching the "corruption of ... gas fee accounting ... token mapping" and fund-draining impact categories in scope. Because this fires on every ordinary gas-abstraction deposit (not an admin action), the blast radius is broader than the original bonding-curve report and recurring rather than one-off.

### Likelihood Explanation
Likelihood is moderate-to-high on an EVM-compatible chain: the triggering condition is simply "the third validator vote transaction that finalizes an inbound ballot is visible in the mempool before inclusion," which is routine, and same-block sandwiching around a specific known transaction is a well-understood MEV technique. The main mitigating factors are pool liquidity depth and the reliance on validator vote timing/ordering, which reduce but do not eliminate feasibility.

### Recommendation
Use a time-weighted average price (TWAP) or otherwise manipulation-resistant oracle to bound `minPCOut` instead of an instantaneous same-block quote, and/or cap per-swap price impact against a longer-window reference price; consider batching/aggregating small deposit swaps to reduce the value-per-sandwich, and evaluate deferring execution or adding commit-reveal/randomized ordering to reduce mempool-visible front-running of the finalizing vote transaction.

### Proof of Concept
1. Attacker monitors the Push Chain mempool/validator vote gossip for the quorum-completing `MsgVoteInbound` for a pending GAS-type inbound with a sizeable `Amount`.
2. Immediately before that transaction is included, attacker submits a large swap on the same PRC20/WPC Uniswap-V3-style pool referenced by `GetUniversalCoreQuoterAddress`/`GetUniversalCoreWPCAddress`, pushing the spot price against the pending deposit's swap direction.
3. The quorum vote lands; `ExecuteInboundGas` calls `GetSwapQuote` (now reflecting the manipulated price) and `CallPRC20DepositAutoSwap` executes at that price, within the mechanical 5% tolerance of the already-bad quote [8](#0-7) .
4. Attacker submits a back-run transaction reversing their initial swap, restoring the pool price and capturing the spread extracted from the deposit's conversion.

Note: I could not fully verify from the indexed code whether any additional pool-selection or liquidity-floor safeguard exists inside the Solidity `UniversalCore`/`depositPRC20WithAutoSwap` contract itself (only the ABI and Go-side caller are indexed here) — a Devin session with full repository access would be needed to confirm whether the on-chain contract adds any TWAP or price-impact guard not visible in the indexed Go code.

### Citations

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L70-155)
```go
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
	if err != nil {
		return errors.Wrap(err, "failed to vote on inbound ballot")
	}

	commit()

	// Voting not finalized yet
	if !isFinalized {
		k.Logger().Debug("vote inbound recorded, ballot not yet finalized",
			"validator", universalValidator.String(),
			"utx_key", universalTxKey,
		)
		return nil
	}

	// --- Ballot finalized: always create UTX from here on ---
	k.Logger().Info("inbound ballot finalized, creating utx", "utx_key", universalTxKey, "source_chain", inbound.SourceChain)

	// Normalize inbound after finalization: strip irrelevant fields, decode raw_payload.
	// If normalization/decode fails, create UTX with failed PCTx + revert.
	if normalizeErr := inbound.NormalizeForTxType(); normalizeErr != nil {
		k.Logger().Warn("inbound normalization failed after ballot finalization",
			"utx_key", universalTxKey,
			"error", normalizeErr.Error(),
		)
		utx := types.UniversalTx{Id: universalTxKey, InboundTx: &inbound}
		if createErr := k.CreateUniversalTx(ctx, universalTxKey, utx); createErr != nil {
			return createErr
		}
		if removeErr := k.RemovePendingInbound(ctx, inbound); removeErr != nil {
			return removeErr
		}
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, normalizeErr); handleErr != nil {
			return handleErr
		}
		return nil
	}

	utx := types.UniversalTx{
		Id:         universalTxKey,
		InboundTx:  &inbound,
		PcTx:       nil,
		OutboundTx: nil,
	}

	// Step 5: Create the UniversalTx — this must succeed for any further processing
	if err := k.CreateUniversalTx(ctx, universalTxKey, utx); err != nil {
		return err
	}

	k.Logger().Info("utx created",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"tx_type", inbound.TxType.String(),
		"amount", inbound.Amount,
	)

	// Step 6: Remove from pending inbound set
	if err := k.RemovePendingInbound(ctx, inbound); err != nil {
		return err
	}

	// Step 7: Validate execution prerequisites.
	// If validation fails, record a failed PCTx and schedule revert (for non-isCEA)
	// instead of failing the vote — so the UTX is always visible on-chain.
	if validationErr := inbound.ValidateForExecution(); validationErr != nil {
		k.Logger().Warn("inbound validation failed, scheduling revert",
			"utx_key", universalTxKey,
			"error", validationErr.Error(),
			"is_cea", inbound.IsCEA,
		)
		if handleErr := k.handleFailedInboundValidation(sdkCtx, utx, validationErr); handleErr != nil {
			return handleErr
		}
		return nil
	}

	// Step 8: Execute the inbound
	k.Logger().Info("dispatching inbound execution",
		"utx_key", universalTxKey,
		"tx_type", inbound.TxType.String(),
	)
	if err := k.ExecuteInbound(ctx, utx); err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L104-153)
```go
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
