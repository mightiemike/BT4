## Finding: Revert outbound built with empty gas fields when asset lacks a registered NativeRepresentation — potential permanently stuck refund

### Summary
`buildRevertOutbound` in `x/uexecutor/keeper/build_revert_outbound.go` degrades silently (log-only) when the deposited asset's token config or `NativeRepresentation` cannot be resolved, or when `GetGasFeeInfoForRevertOutbound` fails, and still emits a `PENDING` `OutboundTx` of type `INBOUND_REVERT` with the correct amount/recipient/destination but with `GasToken`, `GasFee`, `GasPrice`, and `GasLimit` left as zero-value strings. [1](#0-0) 

### Finding Description
`buildRevertOutbound` is invoked from two unprivileged, user-reachable execution paths:
- `handleFailedInboundValidation`, called when `ValidateForExecution` fails *after* the inbound's ballot has already been finalized by honest UVs. [2](#0-1) 
- `ExecuteInboundFunds`, when `depositPRC20` fails for a non-CEA inbound. [3](#0-2) 

Both are downstream of honest-validator ballot finalization of a source-chain deposit event — meaning an unprivileged user can deposit an asset on the source chain whose `AssetAddr` is not present in `uregistry`, or that lacks a `NativeRepresentation.ContractAddress`. The ballot/vote process attests to the *occurrence* of the deposit, not to registry membership, so the inbound can still be finalized and then fail downstream (no PRC20 to mint into), triggering the revert path.

Once triggered, `buildRevertOutbound` populates `DestinationChain`, `Recipient`, `Amount`, `ExternalAssetAddr`, `Sender`, `TxType`, and `Id` correctly, but on `GetTokenConfig`/`GetGasFeeInfoForRevertOutbound` failure it just logs a warning and returns the outbound with **no gas fields**, since these come from `UniversalCore.getOutboundTxGasAndFees`. [4](#0-3) 

### Impact Explanation
If TSS/relayer execution of `INBOUND_REVERT` outbounds relies on `GasToken`/`GasFee`/`GasPrice`/`GasLimit` to fund and price the refund transaction on the source chain, an outbound with these fields empty cannot be correctly executed. The `Amount` field (the user's principal) is preserved, but the mechanism to pay for its return is missing, which could leave the `PENDING` revert outbound permanently unexecutable — freezing user-deposited funds with no automated remediation path.

### Likelihood Explanation
The trigger condition (depositing an asset lacking a registered `NativeRepresentation`) is reachable purely through ordinary, unprivileged source-chain deposits and does not require any malicious validator, relayer, or admin behavior — ballot finalization only attests to the deposit event, not registry membership. However, I was not able to fully trace, within the available tool budget, how `universalClient/` or the TSS/relayer signing path consumes an `OutboundTx` with empty gas fields (e.g., whether a protocol-wide default gas config is substituted, or whether the empty fields make the outbound un-signable/un-broadcastable). The code path clearly documents this as an intentional degraded fallback ("proceeding without gas fields") rather than a crash, which suggests it may have been considered acceptable, but no compensating mechanism (retry-with-backoff, default gas fallback, or a distinct "needs-manual-refund" status) is visible in the reviewed files.

### Recommendation
- Do not silently emit an `OutboundTx` with empty gas fields; either retry the gas lookup, use a documented protocol-level default gas configuration, or mark the outbound/UTX with a distinct status (e.g., `REVERT_GAS_LOOKUP_FAILED`) that can be picked up for remediation instead of `PENDING`.
- Verify in `universalClient/` whether empty `GasFee`/`GasLimit`/`GasPrice`/`GasToken` on an `INBOUND_REVERT` outbound actually blocks TSS signing/broadcast, and if so, add an explicit guard in `buildRevertOutbound` to prevent emitting an unexecutable outbound.

### Proof of Concept
1. Attacker deposits an ERC20/native asset on a supported source chain whose `AssetAddr` has no entry (or no `NativeRepresentation`) in `uregistry`.
2. Honest UVs vote and finalize the ballot for the deposit event (ballot finalization does not check registry membership).
3. `ValidateForExecution` (or `depositPRC20`) fails downstream because no PRC20 mapping exists for the asset.
4. `handleFailedInboundValidation` / `ExecuteInboundFunds` calls `buildRevertOutbound`, which fails `GetTokenConfig`/`GetGasFeeInfoForRevertOutbound` and returns an `OutboundTx` with `GasToken=""`, `GasFee=""`, `GasPrice=""`, `GasLimit=""`.
5. Confirm (in `universalClient/` — not fully traced here) whether this outbound can ever be signed/broadcast by TSS given the missing gas fields; if not, the user's deposited `Amount` remains permanently un-refundable.

**Caveat:** This finding's severity hinges on step 5, which I could not fully confirm within the available investigation budget. Recommend a Devin session with access to `universalClient/` outbound signing logic to confirm whether empty gas fields actually block broadcast before treating this as a confirmed critical/high finding.

### Citations

**File:** x/uexecutor/keeper/build_revert_outbound.go (L27-55)
```go
	// Look up the PRC20 address for this external token
	tokenCfg, err := k.uregistryKeeper.GetTokenConfig(sdkCtx, inbound.SourceChain, inbound.AssetAddr)
	if err != nil || tokenCfg.NativeRepresentation == nil || tokenCfg.NativeRepresentation.ContractAddress == "" {
		k.Logger().Warn("failed to get PRC20 for revert outbound gas lookup, proceeding without gas fields",
			"chain", inbound.SourceChain,
			"asset", inbound.AssetAddr,
			"error", err,
		)
		return outbound
	}

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

	return outbound
```

**File:** x/uexecutor/keeper/handle_failed_inbound_validation.go (L8-47)
```go
// handleFailedInboundValidation records a failed PCTx on the UTX and, for non-isCEA
// inbounds, schedules an INBOUND_REVERT outbound so the user's funds can be returned
// on the source chain. This is called when ValidateForExecution fails after the ballot
// has already been finalized and the UTX created.
func (k Keeper) handleFailedInboundValidation(sdkCtx sdk.Context, utx types.UniversalTx, validationErr error) error {
	inbound := utx.InboundTx
	_, ueModuleAddressStr := k.GetUeModuleAddress(sdkCtx)
	universalTxKey := utx.Id

	k.Logger().Warn("inbound validation failed",
		"utx_key", universalTxKey,
		"source_chain", inbound.SourceChain,
		"is_cea", inbound.IsCEA,
		"error", validationErr.Error(),
	)

	// Record the failed PCTx
	failedPcTx := types.PCTx{
		Sender:      ueModuleAddressStr,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "FAILED",
		ErrorMsg:    validationErr.Error(),
	}

	if err := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(utx *types.UniversalTx) error {
		utx.PcTx = append(utx.PcTx, &failedPcTx)
		return nil
	}); err != nil {
		return err
	}

	// For non-isCEA inbounds, schedule a revert outbound to return funds on source chain.
	// isCEA failures never create an INBOUND_REVERT outbound (consistent with execute_inbound_funds_and_payload.go).
	if !inbound.IsCEA {
		k.Logger().Info("scheduling inbound revert outbound",
			"utx_key", universalTxKey,
			"source_chain", inbound.SourceChain,
			"amount", inbound.Amount,
		)
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
```

**File:** x/uexecutor/keeper/execute_inbound_funds.go (L74-86)
```go
	// isCEA failures never create an INBOUND_REVERT outbound
	// (consistent with execute_inbound_funds_and_payload.go and execute_inbound_gas_and_payload.go)
	if err != nil && !inbound.IsCEA {
		revertOutbound := k.buildRevertOutbound(sdkCtx, inbound)
		if attachErr := k.attachOutboundsToUtx(sdkCtx, utx.Id, []*types.OutboundTx{revertOutbound}, err.Error()); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, utx.Id, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}
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
