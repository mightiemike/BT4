## Verdict: Likely real vulnerability, but scoped to the inbound-execution path (`ExecuteInboundFundsAndPayload`), not the direct `MsgExecutePayload` path.

### Title
Silent Loss of Outbound Withdrawal Records on Multi-Log Receipts With a Mixed Valid/Invalid Outbound Event - (File: `x/uexecutor/keeper/create_outbound.go`, `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`)

### Summary
`BuildOutboundsFromReceipt` aborts and discards its entire in-progress `outbounds` slice as soon as it encounters a single `UniversalTxOutbound` log referencing a disabled chain or unregistered PRC20 token, via early `return nil, err` on `IsChainOutboundEnabled`/`GetTokenConfigByPRC20` failures. [1](#0-0) 

This all-or-nothing behavior is safe when the caller propagates the error and the surrounding Cosmos SDK message handler atomically rolls back all associated state (as happens in `MsgExecutePayload`, `msg_execute_payload.go` lines 116-118, and in the EVM post-tx hook `EVMHooks.PostTxProcessing`, `evm_hooks.go` lines 60-66 — both bubble the error up to abort the whole transaction). [2](#0-1) [3](#0-2) 

However, in `ExecuteInboundFundsAndPayload` (invoked as part of inbound-deposit finalization, not inside an atomic user-message handler), the call to `AttachOutboundsToExistingUniversalTx` (which wraps `BuildOutboundsFromReceipt`) has its error **swallowed**: the error is only recorded into `utx.RevertError` as a string, and the function then falls through and returns `nil`, meaning the caller sees success. [4](#0-3) 

### Finding Description
`ExecutePayloadV2` runs directly on the real `sdkCtx` (not a `CacheContext`) in `ExecuteInboundFundsAndPayload`, so any EVM-level effects of the UEA's payload call — including any PRC20 burns/locks intended to fund the outbound withdrawal — are already committed to state before `AttachOutboundsToExistingUniversalTx` is invoked. [5](#0-4) 

If a user's UEA payload (executed via `ExecutePayloadV2` as part of ordinary cross-chain-deposit + payload processing) emits two `UniversalTxOutbound` logs from `UniversalGatewayPC` in the same receipt — a first legitimate one to an enabled chain/registered token, and a second referencing a disabled chain or unregistered PRC20 — `BuildOutboundsFromReceipt` returns an error on the second log and discards the entire local `outbounds` slice, so **neither** outbound (including the first, otherwise-valid one) is ever attached to the `UniversalTx` or written to `PendingOutbounds`. [6](#0-5) 

Because `ExecuteInboundFundsAndPayload` does not propagate this failure (it stores it only as an informational `RevertError` string on the `UniversalTx` and returns `nil`), no compensating action (e.g., no revert/rescue outbound) is triggered, and the already-committed underlying fund-moving side effects of the payload persist with no corresponding on-chain instruction for TSS to release funds to the destination chain.

### Impact Explanation
This is a permanent-freezing/loss-of-funds scenario for the legitimate first outbound: its withdrawal intent is silently discarded, the `UniversalTx` shows a plain `RevertError` string but no automated remediation is triggered for the valid leg, and (per the code's own comments) rescue paths require an existing `INBOUND_REVERT` outbound in `REVERTED` status or a `FAILED` CEA deposit — neither condition is met here since the PcTx is recorded as `SUCCESS` and no outbound was ever attached. [7](#0-6) 

### Likelihood Explanation
An unprivileged user fully controls the payload executed via their own UEA in the inbound deposit + payload flow, and can therefore control the contract logic that emits multiple `UniversalTxOutbound` events in one transaction (e.g., by calling the gateway twice, once with a valid destination and once with a disabled chain or unregistered token). This requires no privileged actor, malicious validator, or relayer — only ordinary deposit/payload submission — which matches the in-scope "unauthorized state transitions in universal execution flows" and "corruption of ... accounting ... revert destination" impact categories. However, whether this scenario is reachable depends on whether the gateway contract or ordinary user payloads can practically emit two distinct outbound events in a single receipt in the deployed contract logic; I could not fully verify the Solidity contract emitting `UniversalTxOutbound` (it's likely outside the indexed Go code), so I cannot confirm with certainty that the "two-log-one-tx" precondition is achievable through the standard gateway ABI versus requiring an attacker-deployed helper/proxy contract that calls the gateway multiple times (which is still an ordinary unprivileged contract-call pattern, since UEAs can call arbitrary contracts per payload).

### Recommendation
In `BuildOutboundsFromReceipt`, do not abort the entire batch on a single log's failure — either skip the offending log while logging a warning and continue processing remaining logs, or return the successfully-parsed outbounds along with a list of per-log errors so the caller can attach the valid ones and independently handle/revert the invalid one. Additionally, in `ExecuteInboundFundsAndPayload`, do not silently swallow `AttachOutboundsToExistingUniversalTx` errors — either propagate the failure so the whole inbound processing can trigger a proper revert/rescue flow, or ensure a compensating revert/rescue outbound is automatically created when outbound attachment fails after a successful payload execution.

### Proof of Concept
1. An unprivileged user submits a cross-chain deposit + payload to Push Chain such that, upon `ExecutePayloadV2` execution of the UEA payload, the receipt contains two `UniversalTxOutbound` logs from `UniversalGatewayPC`: log #1 targets `ChainId = "eip155:1"` (outbound-enabled, token registered), log #2 targets `ChainId = "eip155:X"` (outbound disabled) or references an unregistered PRC20 address.
2. In `ExecuteInboundFundsAndPayload`, `ExecutePayloadV2` succeeds and commits state (line 290), `payloadPcTx.Status = "SUCCESS"` (line 316), then `AttachOutboundsToExistingUniversalTx` is called (line 318). [8](#0-7) 
3. Inside `BuildOutboundsFromReceipt`, log #1 is parsed and appended to the local `outbounds` slice (line 100); log #2 fails `IsChainOutboundEnabled`/`GetTokenConfigByPRC20` and the function returns `nil, err` (lines 54-67), discarding the in-memory slice that contained the valid outbound #1. [9](#0-8) 
4. `AttachOutboundsToExistingUniversalTx` returns this error without ever calling `attachOutboundsToUtx` (so `PendingOutbounds` is never populated and `utx.OutboundTx` is never appended for either log). [10](#0-9) 
5. Back in `ExecuteInboundFundsAndPayload`, the returned `attachErr` is only stored as `utx.RevertError` (a plain string field), and the function returns `nil` — no error propagates, no rescue/revert outbound is created, and the `UniversalTx`'s `OutboundTx` list remains empty despite log #1 being a fully valid withdrawal intent. [11](#0-10)

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L16-27)
```go
func (k Keeper) BuildOutboundsFromReceipt(
	ctx context.Context,
	utxId string,
	receipt *evmtypes.MsgEthereumTxResponse,
) ([]*types.OutboundTx, error) {

	outbounds := []*types.OutboundTx{}
	universalGatewayPC := strings.ToLower(uregistrytypes.SYSTEM_CONTRACTS["UNIVERSAL_GATEWAY_PC"].Address)

	k.Logger().Debug("building outbounds from receipt", "utx_id", utxId, "tx_hash", receipt.Hash, "log_count", len(receipt.Logs))

	for _, lg := range receipt.Logs {
```

**File:** x/uexecutor/keeper/create_outbound.go (L49-100)
```go
		// Check if outbound is enabled for the destination chain
		outboundEnabled, err := k.uregistryKeeper.IsChainOutboundEnabled(ctx, event.ChainId)
		if err != nil {
			return nil, fmt.Errorf("failed to check outbound enabled for chain %s: %w", event.ChainId, err)
		}
		if !outboundEnabled {
			k.Logger().Warn("outbound disabled for chain", "chain_id", event.ChainId, "utx_id", utxId)
			return nil, fmt.Errorf("outbound is disabled for chain %s", event.ChainId)
		}

		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}

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

		k.Logger().Debug("outbound built from receipt",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"dest_chain", outbound.DestinationChain,
			"amount", outbound.Amount,
			"tx_type", outbound.TxType.String(),
		)
		outbounds = append(outbounds, outbound)
```

**File:** x/uexecutor/keeper/create_outbound.go (L144-155)
```go
func (k Keeper) AttachOutboundsToExistingUniversalTx(
	ctx sdk.Context,
	receipt *evmtypes.MsgEthereumTxResponse,
	utx types.UniversalTx,
) error {
	outbounds, err := k.BuildOutboundsFromReceipt(ctx, utx.Id, receipt)
	if err != nil {
		return err
	}

	return k.attachOutboundsToUtx(ctx, utx.Id, outbounds, "")
}
```

**File:** x/uexecutor/keeper/create_outbound.go (L239-262)
```go
		// Rescue eligibility differs by inbound type:
		//
		//  CEA inbounds: the deposit (first PCTx) must have failed, meaning the funds
		//  never arrived on Push Chain and are still locked on the source chain.
		//
		//  Non-CEA inbounds: the auto-generated INBOUND_REVERT outbound must exist and
		//  have reached REVERTED status, meaning TSS could not return the funds to the
		//  source chain and they are stuck (held by the gateway contract or in escrow).
		if originalUtx.InboundTx.IsCEA {
			if len(originalUtx.PcTx) == 0 || originalUtx.PcTx[0] == nil || originalUtx.PcTx[0].Status != "FAILED" {
				return fmt.Errorf("rescue: UTX %s CEA deposit did not fail", originalUtxId)
			}
		} else {
			hasRevertedAutoRevert := false
			for _, ob := range originalUtx.OutboundTx {
				if ob != nil && ob.TxType == types.TxType_INBOUND_REVERT && ob.OutboundStatus == types.Status_REVERTED {
					hasRevertedAutoRevert = true
					break
				}
			}
			if !hasRevertedAutoRevert {
				return fmt.Errorf("rescue: UTX %s has no reverted inbound-revert outbound", originalUtxId)
			}
		}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L115-121)
```go
	// Step 6: create outbound + UTX only if needed
	if err := k.CreateUniversalTxFromReceiptIfOutbound(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
	if err := k.AttachRescueOutboundFromReceipt(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/evm_hooks.go (L60-66)
```go
	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L285-336)
```go
	ueModuleAddr, _ := k.GetUeModuleAddress(ctx)

	// --- Step 3: execute payload via UEA
	k.Logger().Debug("executing payload via UEA", "utx_key", universalTxKey, "uea", ueaAddr.Hex())
	var payloadErr error
	receipt, payloadErr = k.ExecutePayloadV2(ctx, ueModuleAddr, ueaAddr, utx.InboundTx.UniversalPayload, utx.InboundTx.VerificationData)

	payloadPcTx := types.PCTx{
		Sender:      ueModuleAddressStr,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "FAILED",
	}
	// Capture tx hash from receipt even on EVM revert for debugging.
	if receipt != nil {
		payloadPcTx.TxHash = receipt.Hash
		payloadPcTx.GasUsed = receipt.GasUsed
	}
	if payloadErr != nil {
		k.Logger().Warn("payload execution failed",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"error", payloadErr.Error(),
		)
		payloadPcTx.ErrorMsg = payloadErr.Error()
	} else if receipt != nil {
		k.Logger().Info("payload executed successfully",
			"utx_key", universalTxKey,
			"uea", ueaAddr.Hex(),
			"tx_hash", receipt.Hash,
			"gas_used", receipt.GasUsed,
		)
		payloadPcTx.Status = "SUCCESS"

		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
	}

	updateErr2 := k.UpdateUniversalTx(ctx, universalTxKey, func(utx *types.UniversalTx) error {
		utx.PcTx = append(utx.PcTx, &payloadPcTx)
		return nil
	})
	if updateErr2 != nil {
		return updateErr2
	}

	return nil
```
