### Title
Outbound creation silently drops already-burned PRC20 funds when the destination chain/token pairing is unregistered - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`BuildOutboundsFromReceipt` decodes a `UniversalTxOutbound` event emitted by the `UNIVERSAL_GATEWAY_PC` contract and requires `GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)` to succeed before an `OutboundTx` can be created. This check is performed **after** the EVM transaction that burned/locked the PRC20 on the gateway has already committed state. If the caller supplies a `(ChainId, Token)` combination that has no matching `TokenConfig` in `x/uregistry` — a combination that only the Cosmos-side registry, not the EVM contract, is able to validate — the outbound is never created, no compensating mint-back happens, and the burned PRC20 becomes permanently unrecoverable. This mirrors the reported Kodiak bug class: a swap/bridge step consumes an input asset without validating that the resulting destination-side asset mapping is actually approved, leaving funds stuck.

### Finding Description
`x/uexecutor/keeper/create_outbound.go`'s `BuildOutboundsFromReceipt` [1](#0-0)  resolves the external asset address strictly via the registry lookup `GetTokenConfigByPRC20(ctx, event.ChainId, event.Token)`. If this lookup fails (unregistered token, or a token registered for a different chain than `event.ChainId`), the function returns an error and **no `OutboundTx` is built**.

This function is invoked from two different call sites, both of which run strictly after the EVM state mutation that emitted the log has already been committed:
- `ExecutePayloadV2` commits the UEA/EVM call to a cache context and calls `writeCache()` unconditionally on success, before the caller even attempts to build outbounds [2](#0-1) .
- `execute_inbound_funds_and_payload.go` then calls `AttachOutboundsToExistingUniversalTx` on the already-committed receipt; if it errors, the failure is recorded only as `utx.RevertError` — the EVM burn is not undone and no new outbound is scheduled [3](#0-2) .
- The same class of EVM hook, `EVMHooks.PostTxProcessing`, is invoked as a standard post-tx-execution hook for ordinary `MsgEthereumTx` submissions and calls `CreateUniversalTxFromReceiptIfOutbound` → `BuildOutboundsFromReceipt` after the tx has already executed and its state changes committed [4](#0-3) .

Unlike a failed *observed* outbound — where `handleFailedOutbound` explicitly re-mints the bridged PRC20 back to the sender as compensation [5](#0-4)  — there is no equivalent recovery path when `BuildOutboundsFromReceipt` itself fails to construct the outbound in the first place. The chain-token pairing is validated exclusively on the Cosmos side (`GetTokenConfigByPRC20`), while the actual PRC20 burn on the gateway contract happens on the EVM side in the same already-finalized transaction, with no atomic linkage between the two. Any unprivileged user who can drive an EVM call that triggers the gateway's `UniversalTxOutbound` event (via `MsgExecutePayload` → UEA → gateway call, or by any account submitting a normal EVM transaction that hits the registered `UNIVERSAL_GATEWAY_PC` address) with a `(ChainId, Token)` pair that is not present in `x/uregistry`'s `TokenConfigs` causes their funds to be irreversibly consumed with zero outbound created and only an opaque `RevertError` string persisted.

### Impact Explanation
This results in permanent, unrecoverable loss of user (or module) funds reachable from an ordinary, unprivileged transaction — matching the "Kodiak swap to non-approved token" bug class where an output/destination asset mapping is not validated before an irreversible consuming action occurs. It falls squarely in the in-scope "permanent loss ... of user or protocol-controlled funds" and "corruption of ... token mapping ... or canonical UniversalTx state" categories.

### Likelihood Explanation
Reaching this condition requires only that a `(destination chain, PRC20 address)` combination not be present in `uregistry.TokenConfigs` at the moment the gateway emits the event — e.g. specifying an unsupported/mismatched destination chain for an otherwise valid PRC20, or a PRC20 for a chain pairing that was never registered. This is plausible without any privileged action, since the mapping enforcement lives purely on the Cosmos side and the EVM-side gateway contract (outside this repo) is not shown to re-validate the chain/token pairing before burning.

### Recommendation
Perform the `GetTokenConfigByPRC20` (and `IsChainOutboundEnabled`) validation *before* committing the EVM state that burns/locks the PRC20, or wrap the entire EVM call + outbound-attachment sequence in a single atomic cache context so that a failure to resolve/attach the outbound rolls back the burn. Alternatively, if the check must remain post-hoc, add an explicit compensating mint-back (matching `handleFailedOutbound`'s re-mint logic) whenever `BuildOutboundsFromReceipt`/`AttachOutboundsToExistingUniversalTx` fails, so funds are never silently and permanently stranded.

### Proof of Concept
1. Register a `TokenConfig` for PRC20 `X` only under chain `eip155:1` in `x/uregistry`.
2. Have a user (via `MsgExecutePayload` routed through their UEA, or directly via `MsgEthereumTx`) call the `UNIVERSAL_GATEWAY_PC` contract's withdraw/bridge entry point specifying PRC20 `X` but `ChainId = "eip155:56"` (a chain/token pair with no `TokenConfig`).
3. The gateway contract burns/locks the user's PRC20 `X` and emits `UniversalTxOutbound` with `Token = X`, `ChainId = "eip155:56"`; this EVM transaction commits successfully.
4. `EVMHooks.PostTxProcessing` (or `AttachOutboundsToExistingUniversalTx` from the payload-execution path) calls `BuildOutboundsFromReceipt`, which calls `GetTokenConfigByPRC20(ctx, "eip155:56", X)` — this returns `ErrNotFound` since the pairing was never registered.
5. `BuildOutboundsFromReceipt` returns an error; no `OutboundTx` is ever attached to any `UniversalTx`; the error is only recorded as a string (`RevertError` on the UTX, or simply propagated/logged by the hook). The user's PRC20 `X` remains burned with no path to reclaim it and no outbound ever created on `eip155:56`.

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L59-67)
```go
		// Get the external asset addr
		tokenCfg, err := k.uregistryKeeper.GetTokenConfigByPRC20(
			ctx,
			event.ChainId,
			event.Token, // PRC20 address
		)
		if err != nil {
			return nil, err
		}
```

**File:** x/uexecutor/keeper/execute_payload.go (L39-56)
```go
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

	// Both succeeded — commit EVM state and fee deduction together.
	writeCache()
```

**File:** x/uexecutor/keeper/execute_inbound_funds_and_payload.go (L309-325)
```go
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
```

**File:** x/uexecutor/keeper/evm_hooks.go (L28-67)
```go
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

**File:** x/uexecutor/keeper/outbound.go (L99-147)
```go
// handleFailedOutbound mints back the bridged tokens to the revert recipient,
// then attempts to refund any excess gas (gasFee - gasFeeUsed) just like a
// successful outbound would. Both operations are recorded on the outbound.
func (k Keeper) handleFailedOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	// Only revert bridged funds for funds-related tx types
	if outbound.TxType == types.TxType_FUNDS || outbound.TxType == types.TxType_GAS_AND_PAYLOAD ||
		outbound.TxType == types.TxType_FUNDS_AND_PAYLOAD {

		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}

		amount := new(big.Int)
		amount, ok := amount.SetString(outbound.Amount, 10)
		if !ok {
			return fmt.Errorf("invalid amount: %s", outbound.Amount)
		}
		receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)

		pcTx := types.PCTx{
			Sender:      outbound.Sender,
			BlockHeight: uint64(ctx.BlockHeight()),
		}
		// Capture tx hash from receipt even on EVM revert for debugging.
		if receipt != nil {
			pcTx.TxHash = receipt.Hash
			pcTx.GasUsed = receipt.GasUsed
		}
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
		pcTx.TxHash = receipt.Hash
		pcTx.GasUsed = receipt.GasUsed
		pcTx.Status = "SUCCESS"
		outbound.PcRevertExecution = &pcTx
		k.Logger().Info("outbound failed: funds re-minted for revert",
			"utx_id", utxId,
			"outbound_id", outbound.Id,
			"tx_hash", receipt.Hash,
		)
	}
```
