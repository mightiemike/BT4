## Title
`applyGasRefund` refunds the full `GasFee` (including `ProtocolFee`) back to users on revert-triggered outbounds, discarding the protocol's fee - ([File: x/uexecutor/keeper/gas_fee.go])

### Summary
The `UniversalGatewayPC` contract's `UniversalTxOutbound` event explicitly separates `GasFee` (amount owed to the relayer) from `ProtocolFee` (fee kept by the protocol) — both fields are decoded in `types.UniversalTxOutboundEvent` [1](#0-0) . However, for revert-outbounds, `Keeper.GetOutboundTxGasAndFees` calls the same `UniversalCore.getOutboundTxGasAndFees` view function but explicitly discards `protocolFee` with a comment stating it is "not needed for outbound fields" [2](#0-1) , and only `GasFee`, `GasToken`, `GasPrice`, `GasLimit` are stored on the constructed `OutboundTx` via `buildRevertOutbound` [3](#0-2) .

### Finding Description
`applyGasRefund` computes the excess gas to refund to the user purely as `outbound.GasFee - obs.GasFeeUsed`, where `obs.GasFeeUsed` is the honest-validator-observed amount actually spent on the destination chain [4](#0-3) . If `GasFee` as stored on the outbound is meant to represent the amount reserved for the relayer/gas cost *plus* protocol markup (as the sibling `UniversalTxOutboundEvent.GasFee`/`ProtocolFee` split in the normal outbound-creation path implies protocol fee is a separate, non-refundable component), then dropping `protocolFee` entirely when populating the revert-outbound's `GasFee` field means the protocol's cut is never carved out of the refund base. The full `GasFee` amount (which the contract-side quoting logic computed alongside a `protocolFee` component) is treated as 100% refundable relayer gas, so the excess computed in `applyGasRefund` and paid out via `CallUniversalCoreRefundUnusedGas` includes what should have been the protocol's fee [5](#0-4) .

This mirrors the BakerFi bug pattern precisely: a fee component that is computed and known (`protocolFee`) is silently dropped from the bookkeeping value (`OutboundTx.GasFee`) that later downstream logic (`applyGasRefund`) uses to compute a refund/settlement, causing the protocol to systematically under-collect its intended fee on the INBOUND_REVERT gas-refund path.

### Impact Explanation
Every time an inbound is reverted (a routine, unprivileged, user-reachable flow — reverts happen whenever inbound execution fails, e.g., failed payload execution or insufficient balance) and excess gas exists, the protocol fails to retain `protocolFee` on that outbound's gas settlement, refunding a larger amount to users than intended. This falls within the allowed impact of "corruption of ... gas fee accounting, refund accounting" and results in the protocol receiving a smaller amount of fees than it should — a direct funds-related impact, not merely cosmetic.

### Likelihood Explanation
This triggers on the ordinary, unprivileged `INBOUND_REVERT` path any time revert outbounds are built with a positive protocol fee and unused gas exists after destination execution — a routine occurrence requiring no privileged action, only a normal inbound whose execution needs reverting (e.g., because the recipient contract call fails or funds must bounce back).

### Recommendation
Have `GetOutboundTxGasAndFees` / `GetGasFeeInfoForRevertOutbound` return and propagate `protocolFee` alongside `GasFee`, store it on `OutboundTx` (or subtract it from the refundable `GasFee` before it is persisted), and ensure `applyGasRefund` computes excess strictly against the non-protocol-fee portion of `GasFee`, so the protocol fee is never included in the amount handed back to users via `CallUniversalCoreRefundUnusedGas`.

### Proof of Concept
1. An inbound with `FUNDS_AND_PAYLOAD` fails execution on Push Chain, triggering `buildRevertOutbound`, which calls `GetGasFeeInfoForRevertOutbound` → `GetOutboundTxGasAndFees`, which returns e.g. `gasFee=1000`, `protocolFee=200` (protocol's cut), `gasPrice`, `gasLimit` [6](#0-5) .
2. Only `gasFee=1000` is stored on the revert `OutboundTx.GasFee`; `protocolFee=200` is discarded [7](#0-6) .
3. Validators vote the outbound observation with `GasFeeUsed=700` (actual relayer cost on destination chain).
4. `applyGasRefund` computes `refundAmount = 1000 - 700 = 300` and refunds this full amount to the user via `CallUniversalCoreRefundUnusedGas`, when only `1000 - 200(protocolFee) - 700 = 100` should have been refundable, with `200` reserved for the protocol [8](#0-7) .
5. The protocol's `200` fee is fully given away to the user instead of retained.

Note: I could not locate the Solidity source for `UniversalCore`/`UniversalGatewayPC` in this repository (out of the Go-code scope), so I cannot fully confirm the exact contractual semantics of whether `GasFee` returned by `getOutboundTxGasAndFees` is meant to be inclusive or exclusive of `protocolFee`, or whether the contract itself independently retains `protocolFee` on-chain regardless of what the Go keeper does with the return value. This uncertainty should be verified against the actual UniversalCore contract before treating this as a confirmed, exploitable finding — it is reported based on the explicit "not needed for outbound fields" comment discarding a fee value that the codebase elsewhere treats as a distinct, protocol-retained component [1](#0-0) .

### Citations

**File:** x/uexecutor/types/gateway_pc_event_decode.go (L20-29)
```go
	Amount          *big.Int // amount of Token to bridge
	GasToken        string   // 0x... token used to pay gas fee
	GasFee          *big.Int // amount of GasToken paid to relayer
	GasLimit        *big.Int // gas limit for destination execution
	Payload         string   // 0x-hex calldata
	ProtocolFee     *big.Int // fee kept by protocol
	RevertRecipient string   // where funds go on full revert
	TxType          TxType   // ← single source of truth from proto
	GasPrice        *big.Int // gas price on destination chain at time of outbound
}
```

**File:** x/uexecutor/keeper/gas_fee.go (L26-63)
```go
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
```

**File:** x/uexecutor/keeper/gas_fee.go (L66-76)
```go
// GetGasFeeInfoForRevertOutbound fetches gas info for an INBOUND_REVERT outbound using the
// inbound's PRC20 token address. Returns string values ready for OutboundTx fields.
func (k Keeper) GetGasFeeInfoForRevertOutbound(ctx sdk.Context, prc20Addr string) (gasToken, gasFee, gasPrice, gasLimit string, err error) {
	prc20 := common.HexToAddress(prc20Addr)
	info, err := k.GetOutboundTxGasAndFees(ctx, prc20, big.NewInt(0)) // 0 = use baseLimit
	if err != nil {
		return "", "", "", "", fmt.Errorf("failed to get gas fee info: %w", err)
	}

	return info.GasToken.Hex(), info.GasFee.String(), info.GasPrice.String(), info.GasLimit.String(), nil
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

**File:** x/uexecutor/keeper/outbound.go (L178-256)
```go
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
```
