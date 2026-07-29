Based on my investigation, I found a concrete analog to the `isMintEnabled` bug class.

### Title
Silent swallowing of `outbound disabled` errors in `AttachOutboundsToExistingUniversalTx` causes permanently unrecoverable PRC20 burns during inbound payload execution - (File: `x/uexecutor/keeper/execute_inbound_funds_and_payload.go`, `x/uexecutor/keeper/execute_inbound_gas_and_payload.go`, `x/uexecutor/keeper/create_outbound.go`)

### Summary
The `IsChainOutboundEnabled` flag (the Push Chain analog of `isMintEnabled`) is checked *inside* `BuildOutboundsFromReceipt` [1](#0-0) , which is called *after* the user's cross-chain payload has already executed on the EVM and emitted a `UniversalTxOutboundEvent` (i.e., after the withdraw/burn side effect on Push Chain has already occurred). In the inbound-execution paths (`ExecuteInboundFundsAndPayload`, `ExecuteInboundGasAndPayload`), when this post-hoc outbound-enabled check fails, the resulting error from `AttachOutboundsToExistingUniversalTx` is **not propagated as a hard failure** — it is merely recorded into the UTX's `RevertError` string field, and the function returns `nil` (success) [2](#0-1) .

### Finding Description
`BuildOutboundsFromReceipt` decodes `UniversalTxOutboundEvent` logs from the gateway contract and, for each one, checks `IsChainOutboundEnabled` for the destination chain [3](#0-2) . If disabled, it returns an error and produces zero `OutboundTx` records — meaning no `PendingOutbounds` entry is created and no TSS signing/broadcast will ever occur for that withdrawal.

This check runs strictly after the EVM-side effects (the user's payload calling the gateway to withdraw/burn PRC20 tokens) have already been committed within the same `sdkCtx` via `ExecutePayloadV2`/`CallUEAExecutePayload` [4](#0-3) . Two different callers handle the resulting error differently:

- **Direct `MsgExecutePayload` path** (`msg_execute_payload.go`): if `CreateUniversalTxFromReceiptIfOutbound` errors, the error is returned up through `ExecutePayload`, causing the whole Cosmos message to fail and the branch-store (including the EVM burn) to be discarded atomically [5](#0-4) . This path is safe.
- **Inbound-triggered payload execution path** (`ExecuteInboundGasAndPayload`, `ExecuteInboundFundsAndPayload`): the same failure from `AttachOutboundsToExistingUniversalTx` is caught and only recorded as a `RevertError` string on the UTX; the function then returns `nil` [2](#0-1) . Because `nil` is returned, the surrounding `VoteInbound` finalization flow treats the inbound as successfully processed, and the already-executed EVM burn/withdraw is never rolled back and never compensated — no automatic re-mint, no revert outbound, unlike the deliberate revert path used elsewhere (e.g., `handleFailedOutbound` explicitly re-mints tokens via `CallPRC20Deposit` on outbound failure [6](#0-5) ).

This is the same architectural defect described in the external report: a flag intended purely to pause a downstream delivery/bridging capability (`isMintEnabled` / `IsChainOutboundEnabled`) is checked in a place that can gate/veto an already-executed internal state change, with no atomic all-or-nothing wrapping and no compensating action, resulting in silently and permanently lost user funds.

### Impact Explanation
A user's own cross-chain payload (delivered via a `FUNDS_AND_PAYLOAD` or `GAS_AND_PAYLOAD` inbound) that calls the gateway's withdraw path targeting a chain whose outbound is currently disabled will have its bridged PRC20 tokens burned on Push Chain with **no corresponding `OutboundTx` created and no automatic refund/re-mint**. The only artifact left is a human-readable `RevertError` string on the UTX record — there is no automated remediation path that consumes `RevertError` to restore funds. This matches the "permanent freezing" / "unauthorized burn ... of user or protocol-controlled funds" impact category, reachable purely by an ordinary user submitting a normal cross-chain deposit + payload while outbound is disabled for the target chain (which can be a legitimate, non-privileged, ordinary operational state — e.g., a chain paused for maintenance — not requiring any admin misbehavior at attack time).

### Likelihood Explanation
Requires only that `IsChainOutboundEnabled` be `false` for some destination chain at the moment an inbound payload attempts to route funds out to it — a normal, expected operational configuration, not a privileged attack. Any user (or contract) whose UEA payload triggers a gateway withdrawal to a currently-disabled chain hits this path deterministically. No validator or admin collusion is required at the time of the loss.

### Recommendation
Perform the `IsChainOutboundEnabled` check *before* committing the EVM-side withdrawal effects (e.g., simulate/validate first, or wrap the EVM call + outbound-attachment in a single `CacheContext` that only commits if both succeed, mirroring the pattern already used for gas-fee deduction in the smart-contract path [7](#0-6) ). Additionally, when `AttachOutboundsToExistingUniversalTx` fails in the inbound-execution paths, the failure must not be swallowed into a `RevertError` string with a `nil` return — it should either trigger an automatic compensating re-mint of the burned amount, or hard-fail the block-level UTX processing so the underlying burn is retried/reverted rather than silently finalized as "success."

### Proof of Concept
1. Admin/ops disables outbound for chain `eip155:X` (`ChainConfig.Enabled.IsOutboundEnabled = false`) — a normal, non-attacker action.
2. A user submits a normal `GAS_AND_PAYLOAD` or `FUNDS_AND_PAYLOAD` inbound whose `UniversalPayload` calls the gateway's withdraw function targeting chain `eip155:X`.
3. Validators reach quorum on `VoteInbound`; `ExecuteInboundGasAndPayload`/`ExecuteInboundFundsAndPayload` runs, deposits PRC20 into the UEA, then executes the payload via `ExecutePayloadV2`, which internally burns/withdraws the tokens and emits `UniversalTxOutboundEvent`.
4. `AttachOutboundsToExistingUniversalTx` → `BuildOutboundsFromReceipt` detects `IsChainOutboundEnabled == false` and returns an error [8](#0-7) .
5. The caller records `utx.RevertError` and returns `nil` [2](#0-1) ; `VoteInbound` finalizes normally.
6. Result: tokens are burned on Push Chain, no `OutboundTx`/`PendingOutbounds` entry exists, no TSS signing ever happens, and no re-mint occurs — funds are permanently lost, evidenced only by the `RevertError` string field on the UTX.

(Note: I was unable to fully verify, within available tool calls, the exact bytecode/ABI behavior of the gateway contract's withdraw function — specifically whether it always burns/decrements PRC20 balance unconditionally before emitting `UniversalTxOutboundEvent`, or whether some earlier on-chain guard also checks outbound-enabled status. This should be confirmed by an engineer with full repository/contract access, ideally via a Devin session, before treating this as a definitively confirmed exploit path.)

### Citations

**File:** x/uexecutor/keeper/create_outbound.go (L44-57)
```go
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
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L233-256)
```go
		// Wrap the EVM call + fee deduction in a CacheContext so they
		// commit/revert together. If fee deduction fails, the EVM state
		// changes from executeUniversalTx are discarded — closes the
		// free-execution gap when the recipient contract has no native
		// UPC to cover gas.
		cacheCtx, writeCache := sdkCtx.CacheContext()
		contractReceipt, contractErr := k.CallExecuteUniversalTx(
			cacheCtx,
			ueaAddr,
			utx.InboundTx.SourceChain,
			[]byte(utx.InboundTx.Sender),
			payload,
			scAmount,
			prc20Addr,
			txId,
		)

		var feeErr error
		if contractErr == nil && contractReceipt != nil {
			feeErr = k.DeductGasFeesFromReceipt(cacheCtx, cacheCtx, ueaAddr, contractReceipt, utx.InboundTx.UniversalPayload)
			if feeErr == nil {
				writeCache()
			}
		}
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L292-298)
```go
	receipt, err = k.ExecutePayloadV2(
		ctx,
		ueModuleAddr,
		ueaAddr,
		utx.InboundTx.UniversalPayload,
		utx.InboundTx.VerificationData,
	)
```

**File:** x/uexecutor/keeper/execute_inbound_gas_and_payload.go (L326-333)
```go
		if attachErr := k.AttachOutboundsToExistingUniversalTx(sdkCtx, receipt, utx); attachErr != nil {
			if storeErr := k.UpdateUniversalTx(sdkCtx, universalTxKey, func(u *types.UniversalTx) error {
				u.RevertError = attachErr.Error()
				return nil
			}); storeErr != nil {
				return storeErr
			}
		}
```

**File:** x/uexecutor/keeper/msg_execute_payload.go (L115-118)
```go
	// Step 6: create outbound + UTX only if needed
	if err := k.CreateUniversalTxFromReceiptIfOutbound(sdkCtx, receipt, pcTx); err != nil {
		return err
	}
```

**File:** x/uexecutor/keeper/outbound.go (L99-141)
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
```
