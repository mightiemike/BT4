## Analysis

The Sablier bug class is "fee amounts are computed/charged based on an estimate that assumes full collection, but reconciliation logic (refund) doesn't verify what was actually collected, leading to a mismatch between charged and refunded amounts." Push Chain's `x/uexecutor` module has a structurally identical bug — but instead of merely overpaying, it can mint protocol-controlled value that was never collected in the first place, for `INBOUND_REVERT` outbounds.

### Title
Gas-refund logic mints uncollected value for `INBOUND_REVERT` outbounds - (File: `x/uexecutor/keeper/build_revert_outbound.go`, `x/uexecutor/keeper/outbound.go`)

### Summary
`buildRevertOutbound` populates an `OutboundTx.GasFee` field from a pure **view call** to `UniversalCore.getOutboundTxGasAndFees`, with no accompanying deduction from the sender. Later, `applyGasRefund` treats `outbound.GasFee` as an amount that was actually collected from the user and unconditionally refunds `gasFee - gasFeeUsed` to the recipient once Universal Validators report the real `GasFeeUsed`. For every other outbound type the `GasFee` field originates from a real, already-executed EVM event (`UniversalTxOutboundEventSig` / `RescueFundsOnSourceChain`), but for `INBOUND_REVERT` it never does.

### Finding Description
For regular outbound types (`FUNDS`, `PAYLOAD`, `GAS_AND_PAYLOAD`, `FUNDS_AND_PAYLOAD`, `RESCUE_FUNDS`), `GasFee`/`GasToken`/`GasPrice` are read directly from EVM logs emitted by `UniversalGatewayPC` during `BuildOutboundsFromReceipt`, i.e., they mirror an amount the gateway contract already charged as part of the withdraw/rescue call: [1](#0-0) 

For `INBOUND_REVERT` outbounds, `buildRevertOutbound` instead fetches these fields from `GetOutboundTxGasAndFees`, which is a `view`/staticcall (no state mutation, `CallEVM(..., commit=false, ...)`), executed purely to *estimate* what a relayer would need: [2](#0-1) [3](#0-2) 

Regardless of how `GasFee` was populated, `applyGasRefund` (invoked from both `handleSuccessfulOutbound` and `handleFailedOutbound` during `FinalizeOutbound`) computes `refundAmount = gasFee - gasFeeUsed` and, if positive, calls `CallUniversalCoreRefundUnusedGas` to deposit/swap that amount to the outbound's recipient: [4](#0-3) 

Because the `INBOUND_REVERT` path never collected `outbound.GasFee` from anyone (it is a quoted estimate, not a debited amount), this refund is not "returning excess fee" — it is minting/depositing value to the recipient that has no corresponding prior debit. The refund executes on `refundUnusedGas`, which (per its ABI and usage elsewhere) draws from `UniversalCore`'s PRC20/PC accounting to pay the recipient: [5](#0-4) 

### Impact Explanation
An unprivileged user can force their own inbound to fail (e.g., via `ExecuteInboundGas`/`ExecuteInboundFundsAndPayload` paths where `shouldRevert=true` on deposit/factory/quote failures), which auto-creates an `INBOUND_REVERT` outbound with a `GasFee` quoted from `getOutboundTxGasAndFees` using the contract's conservative `baseLimit`: [6](#0-5) [7](#0-6) 

When honest Universal Validators later report the real destination-chain gas consumed via `MsgVoteOutbound` (`GasFeeUsed`, which will typically be well below the conservative base-limit-derived quote), `applyGasRefund` mints the difference to the attacker's own recipient address — funds that the attacker never paid. Repeating this (self-triggered inbound failure → revert outbound → gas refund) drains protocol-controlled PRC20/PC reserves with no offsetting debit, satisfying the "unauthorized mint" / "draining of protocol-controlled funds" impact criteria.

### Likelihood Explanation
Triggering a deposit/factory-lookup failure on one's own inbound (e.g., malformed CAIP-2 metadata, unsupported token combination, or a deliberately-failing swap quote) is plausible for an unprivileged sender and does not require any validator or admin misbehavior — only honest UV voting on the resulting revert outbound is needed for the refund to fire.

### Recommendation
Do not apply `applyGasRefund` to outbound types whose `GasFee` was not actually collected from a party. Either (a) skip refund logic entirely for `TxType_INBOUND_REVERT` (and any other type sourced from a view-only quote rather than an on-chain deduction event), or (b) require `buildRevertOutbound` to record whether the quoted fee was actually pre-funded/escrowed before `applyGasRefund` is allowed to run against it, mirroring the invariant already respected for `FUNDS`/`PAYLOAD` outbounds whose `GasFee` originates from a real withdraw event.

### Proof of Concept
1. Attacker submits an inbound whose observed vote will make `ExecuteInboundFundsAndPayload`/`ExecuteInboundGas` fail deterministically (e.g., referencing a token/chain combination that fails `GetTokenConfig`, or where `CallFactoryToGetUEAAddressForOrigin`/swap-quote calls error) with `shouldRevert = true`.
2. `buildRevertOutbound` is invoked, populating `GasFee` from `getOutboundTxGasAndFees` (a view call, no debit) — see `x/uexecutor/keeper/build_revert_outbound.go:38-53`.
3. Honest UVs sign/broadcast the revert on the source chain and vote `MsgVoteOutbound` with the true `GasFeeUsed` (lower than the quoted base-limit `GasFee`).
4. `FinalizeOutbound` → `handleFailedOutbound`/`handleSuccessfulOutbound` → `applyGasRefund` computes `refundAmount = GasFee - GasFeeUsed > 0` and calls `CallUniversalCoreRefundUnusedGas`, depositing/swapping that amount to the attacker's recipient — see `x/uexecutor/keeper/outbound.go:174-257`.
5. Repeat with additional self-triggered failing inbounds to accumulate uncollected refunds.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L69-91)
```go
		outbound := &types.OutboundTx{
			DestinationChain:  event.ChainId,
			Recipient:         event.Target,
			Amount:            event.Amount.String(),
			ExternalAssetAddr: tokenCfg.Address,
			Prc20AssetAddr:    event.Token,
			Sender:            event.Sender,
			Payload:           event.Payload,
			GasFee:            event.GasFee.String(),
			GasLimit:          event.GasLimit.String(),
			GasPrice:          event.GasPrice.String(),
			GasToken:          event.GasToken,
			TxType:            event.TxType,
			PcTx: &types.OriginatingPcTx{
				TxHash:   receipt.Hash,
				LogIndex: fmt.Sprintf("%d", lg.Index),
			},
			RevertInstructions: &types.RevertInstructions{
				FundRecipient: event.RevertRecipient,
			},
			OutboundStatus: types.Status_PENDING,
			Id:             strings.TrimPrefix(event.TxID, "0x"),
		}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L38-53)
```go
	// Fetch gas fields from UniversalCore.getOutboundTxGasAndFees(prc20, 0)
	// 0 means use the contract's baseLimit for this chain
	gasToken, gasFee, gasPrice, gasLimit, err := k.GetGasFeeInfoForRevertOutbound(sdkCtx, tokenCfg.NativeRepresentation.ContractAddress)
	if err != nil {
		k.Logger().Warn("failed to fetch gas fee info for revert outbound, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"prc20", tokenCfg.NativeRepresentation.ContractAddress,
			"error", err,
		)
		return outbound
	}

	outbound.GasToken = gasToken
	outbound.GasFee = gasFee
	outbound.GasPrice = gasPrice
	outbound.GasLimit = gasLimit
```

**File:** x/uexecutor/keeper/gas_fee.go (L23-64)
```go
// GetOutboundTxGasAndFees calls UniversalCore.getOutboundTxGasAndFees(prc20, gasLimitWithBaseLimit)
// to get gasToken, gasFee, protocolFee, gasPrice, and chainNamespace.
// Pass gasLimitWithBaseLimit=0 to use the contract's baseLimit.
func (k Keeper) GetOutboundTxGasAndFees(ctx sdk.Context, prc20 common.Address, gasLimitWithBaseLimit *big.Int) (*GasFeeInfo, error) {
	handlerAddr := common.HexToAddress(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_CORE"].Address)

	ucABI, err := types.ParseUniversalCoreABI()
	if err != nil {
		return nil, errors.Wrap(err, "failed to parse UniversalCore ABI")
	}

	ueModuleAccAddress, _ := k.GetUeModuleAddress(ctx)

	receipt, err := k.evmKeeper.CallEVM(ctx, ucABI, ueModuleAccAddress, handlerAddr, false, nil,
		"getOutboundTxGasAndFees", prc20, gasLimitWithBaseLimit)
	if err != nil {
		return nil, errors.Wrap(err, "failed to call getOutboundTxGasAndFees")
	}

	results, err := ucABI.Methods["getOutboundTxGasAndFees"].Outputs.Unpack(receipt.Ret)
	if err != nil {
		return nil, errors.Wrap(err, "failed to unpack getOutboundTxGasAndFees result")
	}

	gasToken := results[0].(common.Address)
	gasFee := results[1].(*big.Int)
	// protocolFee := results[2].(*big.Int) — not needed for outbound fields
	gasPrice := results[3].(*big.Int)
	// chainNamespace := results[4].(string) — not needed for outbound fields
	// gasLimitUsed (results[5]) is the exact gas limit the contract resolved
	// (caller-supplied or per-chain baseGasLimitByChainNamespace fallback).
	// Reading it directly avoids the gasFee/gasPrice round-trip and keeps us
	// in lock-step with the contract's own resolution.
	gasLimit := results[5].(*big.Int)

	return &GasFeeInfo{
		GasToken: gasToken,
		GasFee:   gasFee,
		GasPrice: gasPrice,
		GasLimit: gasLimit,
	}, nil
}
```

**File:** x/uexecutor/keeper/outbound.go (L174-257)
```go
// applyGasRefund computes the excess gas (gasFee - gasFeeUsed) and, if positive,
// calls UniversalCore refundUnusedGas. The result is recorded in outbound.PcRefundExecution.
// It is called for both successful and failed outbounds — gas is consumed on the
// external chain regardless of execution outcome.
func (k Keeper) applyGasRefund(ctx sdk.Context, outbound *types.OutboundTx, obs *types.OutboundObservation) {
	if obs.GasFeeUsed == "" || outbound.GasFee == "" || outbound.GasToken == "" {
		return
	}

	gasFee := new(big.Int)
	if _, ok := gasFee.SetString(outbound.GasFee, 10); !ok {
		return
	}

	gasFeeUsed := new(big.Int)
	if _, ok := gasFeeUsed.SetString(obs.GasFeeUsed, 10); !ok {
		return
	}

	// No excess gas to refund
	if gasFee.Cmp(gasFeeUsed) <= 0 {
		return
	}

	refundAmount := new(big.Int).Sub(gasFee, gasFeeUsed)
	gasToken := common.HexToAddress(outbound.GasToken)

	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)

	refundPcTx := &types.PCTx{
		Sender:      outbound.Sender,
		BlockHeight: uint64(ctx.BlockHeight()),
	}

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

**File:** x/uexecutor/keeper/execute_inbound_gas.go (L192-208)
```go
	if execErr != nil && shouldRevert {
		revertOutbound := k.buildRevertOutbound(sdkCtx, &inbound)

		if attachErr := k.attachOutboundsToUtx(
			sdkCtx,
			universalTxKey,
			[]*types.OutboundTx{revertOutbound},
			revertReason,
		); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L187-205)
```go
	// If deposit failed, stop here.
	if execErr != nil {
		if shouldRevert {
			revertOutbound := k.buildRevertOutbound(sdkCtx, utx.InboundTx)
			if attachErr := k.attachOutboundsToUtx(
				sdkCtx,
				universalTxKey,
				[]*types.OutboundTx{revertOutbound},
				revertReason,
			); attachErr != nil {
				if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
					u.RevertError = attachErr.Error()
					return nil
				}); storeErr != nil {
					return storeErr
				}
			}
		}
		return nil
```
