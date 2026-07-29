### Title
Unbounded outbound accumulation in `BuildOutboundsFromReceipt`/`attachOutboundsToUtx` allows a single user payload to bloat a `UniversalTx` with unbounded gateway outbound events - ([File: x/uexecutor/keeper/create_outbound.go])

### Summary
`x/uexecutor/keeper/create_outbound.go`'s `BuildOutboundsFromReceipt` iterates over every log in an EVM receipt with no limit on how many matching `UniversalTxOutboundEvent` entries it will convert into `OutboundTx` records, and `attachOutboundsToUtx` appends all of them onto a single `UniversalTx.OutboundTx` slice. Because the receipt comes from executing an attacker-supplied `UniversalPayload` through the user's own UEA (`ExecutePayloadV2` / `CallUEAExecutePayload`), an unprivileged user can craft a payload whose execution calls the `UNIVERSAL_GATEWAY_PC` withdraw entrypoint many times in one transaction, causing many outbound events to be attached to one `UniversalTx` and one `PendingOutbounds` write per event, all with no protocol-enforced cap.

### Finding Description
`BuildOutboundsFromReceipt` loops over `receipt.Logs` unconditionally: [1](#0-0)  for each log matching the `UniversalGatewayPC` address and the `UniversalTxOutboundEventSig` topic, it decodes an outbound event and appends a fully-formed `OutboundTx` to the result slice with no size limit: [2](#0-1) .

This function is invoked from the standard, unprivileged execution paths:
- `ExecutePayloadV2` runs an arbitrary attacker-controlled `UniversalPayload` through the caller's UEA via `CallUEAExecutePayload`, and the resulting receipt is passed straight into `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt`: [3](#0-2)  and [4](#0-3) .
- Direct `MsgExecutePayload` also feeds the receipt into `CreateUniversalTxFromReceiptIfOutbound` and `AttachRescueOutboundFromReceipt`, both of which call `BuildOutboundsFromReceipt`: [5](#0-4) .
- The generic EVM post-processing hook applies the same unbounded logic to every EVM transaction on the chain: [6](#0-5) .

All discovered outbounds are then appended to the target `UniversalTx.OutboundTx` slice inside `attachOutboundsToUtx`, and each individually writes an entry to `PendingOutbounds` and emits an event, all within a single state-machine update with no cap on `len(outbounds)`: [7](#0-6) .

The `UniversalPayload.GasLimit`/`Value`/etc. fields are only validated as well-formed unsigned integers — I found no enforced ceiling on `GasLimit` in `ValidateBasic`: [8](#0-7) . Since payload execution is billed through the normal Cosmos EVM gas metering (`CallUEAExecutePayload` inside a real EVM call), the number of gateway calls (hence outbound events) an attacker can pack into one transaction is bounded only by the chain's block gas limit divided by the cost of a single gateway withdraw call — not by any application-level cap in `x/uexecutor`. This mirrors the dForce bug class: a per-transaction loop whose iteration count is controlled by attacker-chosen inputs/state rather than a protocol-enforced maximum.

### Impact Explanation
Each outbound attached to the `UniversalTx` triggers: an entry in the growing `OutboundTx` slice (bloating the single `UniversalTx` record that is repeatedly read/written/marshaled by `UpdateUniversalTx`, queried by users, and iterated in later flows such as `AttachRescueOutboundFromReceipt`'s scan over `originalUtx.OutboundTx`), an independent `PendingOutbounds` KV entry (state growth), and downstream TSS-signing coordination work per outbound (each destined for eventual signature by the TSS coordinator). A single attacker-controlled transaction can therefore:
- Bloat on-chain state proportional to attacker-chosen loop count rather than a fixed protocol bound (unbounded, unprivileged state growth), and
- Increase the cost of every later read/update of that specific `UniversalTx` (larger protobuf payload to marshal/unmarshal on every `UpdateUniversalTx` call touching it), degrading querying and processing of that UTX, and adding load to the TSS outbound-signing pipeline that must eventually process every attached outbound.

This is analogous to, but weaker than, the dForce report's block-gas-limit DoS: unlike `calcAccountEquity`, this repository's `BuildOutboundsFromReceipt` executes within the bounds of a single EVM transaction's own gas budget (so it cannot itself exceed the block gas limit in one shot), and does not appear to block a specific privileged action (like liquidation) for other users. The concrete, verifiable impact is unbounded state growth and increased future processing cost tied to one `UniversalTx`/UEA, not a proven denial of a specific in-scope action (transfer/redeem/liquidation-equivalent) for other users.

### Likelihood Explanation
Reaching this code path requires only a normal user to submit `MsgExecutePayload` (or an inbound funds+payload flow) with a `UniversalPayload.Data` that calls the `UNIVERSAL_GATEWAY_PC` contract's withdraw entrypoint many times (e.g., via a helper contract loop) within one transaction's gas allotment — no privileged role is needed. However, I was unable to confirm (within the code reviewed) the exact cost of a single gateway withdraw call, the chain's configured block gas limit, or whether any other layer (e.g., EVM tx gas limit ante checks, `MaxGasWanted`) further restricts the number of repeatable calls, so the concrete achievable count of outbounds per transaction is unverified.

### Recommendation
Enforce an explicit cap on the number of `OutboundTx` entries that can be attached to a single `UniversalTx` per receipt (and/or across the UTX's lifetime) in `BuildOutboundsFromReceipt`/`attachOutboundsToUtx`, rejecting or truncating with an error once the cap is exceeded, similar to how `PerChainCap` already bounds in-flight TSS sign events. Consider also bounding total `UniversalTx` size (e.g., max `len(OutboundTx)` + `len(PcTx)`) to protect state growth and future read/update costs.

### Proof of Concept
Not independently reproduced. Conceptually: deploy a helper contract that repeatedly calls `UniversalGatewayPC`'s withdraw method in a loop within a single call, then submit a `UniversalPayload` (via `MsgExecutePayload`) whose `To`/`Data` targets this helper through the caller's UEA. If the per-call gas cost allows dozens/hundreds of iterations under the transaction's gas budget, `BuildOutboundsFromReceipt` will convert every one of those events into an `OutboundTx` appended to the resulting `UniversalTx`, which can be verified by querying the UTX afterward and observing `len(utx.OutboundTx)` matching the loop count with no rejection at any cap. This was not run against a live node as part of this investigation; the code paths and absence of a cap are directly confirmed by inspection.

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

**File:** x/uexecutor/keeper/create_outbound.go (L40-101)
```go
		if strings.ToLower(lg.Topics[0]) != strings.ToLower(types.UniversalTxOutboundEventSig) {
			continue
		}

		event, err := types.DecodeUniversalTxOutboundFromLog(lg)
		if err != nil {
			return nil, fmt.Errorf("failed to decode UniversalTxWithdraw: %w", err)
		}

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
	}
```

**File:** x/uexecutor/keeper/create_outbound.go (L141-155)
```go
// AttachOutboundsToExistingUniversalTx
// Used when UniversalTx already exists (e.g. inbound execution)
// It attaches outbounds extracted from receipt to the existing utx.
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

**File:** x/uexecutor/keeper/create_outbound.go (L339-371)
```go
func (k Keeper) attachOutboundsToUtx(
	ctx sdk.Context,
	utxId string,
	outbounds []*types.OutboundTx,
	revertMsg string, // revert msg if the outbound is for a inbound revert
) error {

	if len(outbounds) == 0 {
		return nil
	}
	return k.UpdateUniversalTx(ctx, utxId, func(utx *types.UniversalTx) error {

		for _, outbound := range outbounds {

			utx.OutboundTx = append(utx.OutboundTx, outbound)

			// Compute signature expiry deadline for the destination chain.
			var signingDeadline int64
			if chainCfg, err := k.uregistryKeeper.GetChainConfig(ctx, outbound.DestinationChain); err == nil {
				if chainCfg.TssSigningDeadline != nil && *chainCfg.TssSigningDeadline > 0 {
					signingDeadline = ctx.BlockTime().Unix() + int64(chainCfg.TssSigningDeadline.Seconds())
				}
			}

			// Write to pending outbounds index (inside UpdateUniversalTx closure for atomicity)
			if err := k.PendingOutbounds.Set(ctx, outbound.Id, types.PendingOutboundEntry{
				OutboundId:      outbound.Id,
				UniversalTxId:   utxId,
				CreatedAt:       ctx.BlockHeight(),
				SigningDeadline: signingDeadline,
			}); err != nil {
				return fmt.Errorf("failed to set pending outbound index for %s: %w", outbound.Id, err)
			}
```

**File:** x/uexecutor/keeper/execute_payload.go (L35-53)
```go
	// Step 2: Wrap EVM execution + fee deduction in a CacheContext so they
	// commit/revert together. If fee deduction fails, the EVM state changes
	// from CallUEAExecutePayload are discarded — closes the free-execution
	// gap when the UEA has no native UPC to cover gas.
	cacheCtx, writeCache := sdkCtx.CacheContext()
	receipt, execErr := k.CallUEAExecutePayload(cacheCtx, evmFrom, ueaAddr, universalPayload, verificationDataVal)

	// Step 3: Try fee deduction in the same cache. DeductGasFeesFromReceipt
	// is a no-op if the receipt is nil or GasUsed == 0 (EVM call produced
	// nothing to bill).
	if feeErr := k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, receipt, universalPayload); feeErr != nil {
		// Cache discarded — EVM state and any partial fee work both roll back.
		return receipt, fmt.Errorf("gas fee deduction failed: %w", feeErr)
	}

	if execErr != nil {
		// EVM execution failed — cache discarded by not calling writeCache.
		return receipt, execErr
	}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L106-124)
```go
	// Step 5
	pcTx := types.PCTx{
		Sender:      evmFrom.Hex(),
		TxHash:      receipt.Hash,
		GasUsed:     receipt.GasUsed,
		BlockHeight: uint64(sdkCtx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Step 6: create outbound + UTX only if needed
	if err := k.CreateUniversalTxFromReceiptIfOutbound(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
	if err := k.AttachRescueOutboundFromReceipt(sdkCtx, receipt, pcTx); err != nil {
		return err
	}

	return nil
}
```

**File:** x/uexecutor/keeper/evm_hooks.go (L25-67)
```go
// PostTxProcessing is called by the EVM module after transaction execution.
// It inspects the receipt and creates UniversalTx + Outbound only if
// UniversalTxWithdraw event is detected.
func (h EVMHooks) PostTxProcessing(
	ctx sdk.Context,
	sender common.Address,
	msg core.Message,
	receipt *ethtypes.Receipt,
) error {
	if receipt == nil || len(receipt.Logs) == 0 {
		return nil
	}

	h.k.Logger().Debug("evm hook post-tx processing",
		"tx_hash", receipt.TxHash.Hex(),
		"sender", sender.Hex(),
		"log_count", len(receipt.Logs),
		"gas_used", receipt.GasUsed,
	)

	protoReceipt := &evmtypes.MsgEthereumTxResponse{
		Hash:    receipt.TxHash.Hex(),
		GasUsed: receipt.GasUsed,
		Logs:    convertReceiptLogs(receipt.Logs),
	}

	// Build pcTx representation
	pcTx := types.PCTx{
		Sender:      sender.Hex(),
		TxHash:      protoReceipt.Hash,
		GasUsed:     protoReceipt.GasUsed,
		BlockHeight: uint64(ctx.BlockHeight()),
		Status:      "SUCCESS",
	}

	// Handle normal outbounds (UniversalTxOutbound events → new UTX + outbounds).
	if err := h.k.CreateUniversalTxFromReceiptIfOutbound(ctx, protoReceipt, pcTx); err != nil {
		return err
	}

	// Handle rescue outbounds (RescueFundsOnSourceChain events → attach to original UTX).
	return h.k.AttachRescueOutboundFromReceipt(ctx, protoReceipt, pcTx)
}
```

**File:** x/uexecutor/types/universal_payload.go (L24-65)
```go
// ValidateBasic does the sanity check on the UniversalPayload fields.
func (p UniversalPayload) ValidateBasic() error {
	// Validate 'to' address
	if strings.TrimSpace(p.To) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "to address cannot be empty")
	}
	if !utils.IsValidAddress(p.To, utils.HEX) {
		return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid to address format: %s", p.To)
	}

	// Validate 'data' is a valid hex string
	if len(p.Data) > 0 {
		if _, err := hex.DecodeString(strings.TrimPrefix(p.Data, "0x")); err != nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "invalid hex data")
		}
	}

	// Validate all numeric string fields as uint256
	uintFields := map[string]string{
		"value":                    p.Value,
		"gas_limit":                p.GasLimit,
		"max_fee_per_gas":          p.MaxFeePerGas,
		"max_priority_fee_per_gas": p.MaxPriorityFeePerGas,
		"nonce":                    p.Nonce,
		"deadline":                 p.Deadline,
	}

	for fieldName, value := range uintFields {
		if value != "" {
			bi, ok := new(big.Int).SetString(value, 10)
			if !ok || bi.Sign() < 0 {
				return errors.Wrapf(sdkerrors.ErrInvalidRequest, "%s must be a valid unsigned integer", fieldName)
			}
		}
	}

	if _, ok := VerificationType_name[int32(p.VType)]; !ok {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "invalid verificationData type: %v", p.VType)
	}

	return nil
}
```
