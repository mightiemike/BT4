### Title
Attacker-controlled `RevertInstructions.FundRecipient` / outbound `Sender` used as an unvalidated push-target for `depositPRC20Token`, letting a single revert permanently abort fund recovery - (File: `x/uexecutor/keeper/outbound.go`)

### Summary
The external report is a "push over pull" class bug: a privileged funds-movement path unconditionally *pushes* tokens to an attacker-influenced address, and if that transfer reverts (e.g. blocklisted recipient), the whole recovery mechanism breaks. The Push Chain analog lives in `handleFailedOutbound`, which re-mints bridged funds by pushing PRC20 tokens to `outbound.Sender` or `outbound.RevertInstructions.FundRecipient` — both of which are attacker/user-supplied fields taken verbatim from the original inbound event on the *external* source chain — via `k.CallPRC20Deposit`. If that EVM call reverts for any reason tied to the recipient address, the code does not retry with a safe fallback; it permanently marks the outbound `ABORTED`, requiring manual admin intervention with no automatic pull-based recovery path.

### Finding Description
`handleFailedOutbound` in [1](#0-0)  computes the revert recipient directly from attacker-influenced inbound data:

```go
recipient := outbound.Sender
if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
    recipient = outbound.RevertInstructions.FundRecipient
}
...
receipt, err := k.CallPRC20Deposit(ctx, common.HexToAddress(outbound.Prc20AssetAddr), common.HexToAddress(recipient), amount)
``` [2](#0-1) 

`RevertInstructions.FundRecipient` and `Sender` originate from the `Inbound`/`OutboundTx` created from a user-controlled source-chain gateway event [3](#0-2)  and are echoed straight through `buildRevertOutbound`/`create_outbound.go` into the outbound record without any allowlist/validation of the destination address's semantics beyond hex-parsing.

If `k.CallPRC20Deposit` (a "push" call — module-originated `DerivedEVMCall` to `depositPRC20Token` on the PRC20/UniversalCore contract, [4](#0-3) ) reverts because of anything tied to the destination address (e.g. a contract that reverts on receipt, a paused/capped token state, or any other address-dependent revert condition in the PRC20 implementation — which is out of scope of this Go repo but is invoked here exactly as a push), the keeper does not attempt any pull-based fallback. It goes straight to:

```go
if err != nil {
    pcTx.Status = "FAILED"
    ...
    return k.AbortOutbound(ctx, utxId, outbound,
        fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
}
``` [5](#0-4) 

`AbortOutbound` marks the outbound `Status_ABORTED` and removes it from `PendingOutbounds` [6](#0-5) , meaning the automatic UV-driven refund pipeline never retries. The only path forward is `AttachRescueOutboundFromReceipt`/rescue flow, which is explicitly gated to `isCEA` deposit-failure scenarios or reverted `INBOUND_REVERT` outbounds and is not wired to retrigger for an *outbound*-level ABORTED re-mint failure (its precondition checks are about `PcTx[0]` deposit status, not `OutboundTx` revert-mint failures) [7](#0-6) . There is no user-callable "pull" endpoint that lets the affected recipient reclaim the funds once this push fails — recovery depends entirely on manual admin action outside the scoped protocol logic.

The identical push pattern exists in `applyGasRefund` for the excess-gas refund leg, which also resolves the refund recipient from `outbound.RevertInstructions.FundRecipient`/`outbound.Sender` [8](#0-7)  and calls `CallUniversalCoreRefundUnusedGas` — though its failure path only sets `Status="FAILED"` on the refund PCTx rather than aborting the whole outbound.

### Impact Explanation
If the module-originated push to the attacker-influenced recipient address reverts, the failed-outbound funds-recovery flow (`handleFailedOutbound`) is permanently short-circuited into `ABORTED` with no automatic remediation path in the reachable, unprivileged flow. This is a **permanent freezing of user/protocol-controlled funds** for that UTX: the bridged tokens that should have been re-minted back to the sender/fund-recipient are stuck, and normal Universal-Validator-driven finalization can no longer make progress on this outbound. This matches the in-scope impact category "permanent freezing... of user or protocol-controlled funds" and "denial of service... reachable without privileged control," since the trigger condition depends solely on the destination address supplied by the original (attacker-controlled) inbound event.

### Likelihood Explanation
Low-to-moderate. It requires: (1) an outbound to first fail (attacker/relayer can influence this by causing execution failure on the destination chain), and (2) the deposit-back call to revert specifically because of the recipient address. Whether the PRC20/UniversalCore contract can be made to revert `depositPRC20Token` based on an attacker-chosen recipient (e.g., a malicious contract, a paused-per-address flag, or a cap that this particular re-mint would exceed) depends on Solidity contract internals that are outside this Go/Cosmos repository and could not be verified here — this is the same class of trigger as the original report (a blocklisted/hostile recipient), applied to Push Chain's re-mint push instead of a vesting-contract push.

### Recommendation
- Do not let a single reverted re-mint/refund call permanently abort recovery with no retry path reachable by an unprivileged user. Wrap `CallPRC20Deposit`/`CallUniversalCoreRefundUnusedGas` failures in a genuinely automated retry or escrow ("pull") mechanism scoped to the affected recipient, rather than requiring privileged admin intervention as the sole exit from `ABORTED`.
- Consider decoupling the re-mint destination from unauthenticated inbound-supplied strings: validate `RevertInstructions.FundRecipient` more strictly, or hold failed re-mints in an escrow balance keyed by the original UTX that the rightful owner can withdraw via a dedicated claim message, rather than only via one push attempt.
- Extend the existing `RESCUE_FUNDS` rescue mechanism to also cover ABORTED `handleFailedOutbound` re-mint failures, so there is at least an existing on-chain remediation route without requiring an off-protocol admin action outside the scoped modules.

### Proof of Concept
Conceptual trace (cannot be fully executed without the PRC20/UniversalCore Solidity source, which lives outside this repository):
1. Attacker triggers a `FUNDS`/`FUNDS_AND_PAYLOAD`/`GAS_AND_PAYLOAD` inbound with `RevertInstructions.FundRecipient` set to an address they control that is engineered to make `depositPRC20Token` revert for that specific target (e.g., an address flagged in a per-recipient cap/pause check inside the PRC20/UniversalCore contract).
2. The corresponding outbound (created via `BuildOutboundsFromReceipt`, [9](#0-8) ) later fails and Universal Validators vote it via `MsgVoteOutbound` with `success=false`.
3. `FinalizeOutbound` → `handleFailedOutbound` attempts `CallPRC20Deposit(prc20, recipient, amount)` to that engineered `recipient` [10](#0-9) .
4. The call reverts; the outbound is marked `ABORTED` via `AbortOutbound` [11](#0-10)  and removed from `PendingOutbounds`, leaving the bridged funds unrecoverable through the normal protocol flow.

### Citations

**File:** x/uexecutor/keeper/outbound.go (L45-69)
```go
// AbortOutbound marks an outbound as ABORTED with a reason.
// This signals that automatic processing has failed and manual intervention is needed.
func (k Keeper) AbortOutbound(ctx context.Context, utxId string, outbound types.OutboundTx, reason string) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	outbound.OutboundStatus = types.Status_ABORTED
	outbound.AbortReason = reason

	if err := k.UpdateOutbound(ctx, utxId, outbound); err != nil {
		return err
	}

	// Defensively remove from pending index (may already be removed by caller)
	_ = k.PendingOutbounds.Remove(ctx, outbound.Id)

	// Emit event for monitoring/alerting
	sdkCtx.EventManager().EmitEvent(sdk.NewEvent(
		"outbound_aborted",
		sdk.NewAttribute("utx_id", utxId),
		sdk.NewAttribute("outbound_id", outbound.Id),
		sdk.NewAttribute("abort_reason", reason),
	))

	return nil
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

**File:** x/uexecutor/keeper/outbound.go (L201-206)
```go
	// Refund recipient: prefer fund_recipient in revert_instructions, fall back to sender
	refundRecipient := outbound.Sender
	if outbound.RevertInstructions != nil && outbound.RevertInstructions.FundRecipient != "" {
		refundRecipient = outbound.RevertInstructions.FundRecipient
	}
	recipientAddr := common.HexToAddress(refundRecipient)
```

**File:** proto/uexecutor/v1/types.proto (L95-100)
```text
message RevertInstructions {
  option (amino.name) = "uexecutor/revert_instructions";
  option (gogoproto.equal) = true;

  string fund_recipient = 1;       // where funds go in revert/refund
}
```

**File:** x/uexecutor/keeper/evm.go (L261-303)
```go
// Calls Handler Contract to deposit prc20 tokens
func (k Keeper) CallPRC20Deposit(
	ctx sdk.Context,
	prc20Address, to common.Address,
	amount *big.Int,
) (*evmtypes.MsgEthereumTxResponse, error) {
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
		ueModuleAccAddress, // sender: module account
		handlerAddr,        // destination
		big.NewInt(0),
		nil,
		true,   // commit = true (real tx, not simulation)
		false,  // gasless = false (@dev: we need gas to be emitted in the tx receipt)
		true,   // module sender = true
		&nonce, // manual nonce of module
		"depositPRC20Token",
		prc20Address,
		amount,
		to,
	)
}
```

**File:** test/integration/uexecutor/rescue_funds_test.go (L212-230)
```go
	t.Run("rescue is rejected for non-CEA inbound with no reverted auto-revert", func(t *testing.T) {
		// Non-CEA FUNDS inbound: minting succeeds, so no INBOUND_REVERT outbound exists.
		// Rescue must be rejected because the auto-revert has not been attempted and reverted.
		chainApp, ctx, vals, inbound, coreVals := setupInboundBridgeTest(t, 4)

		for i := 0; i < 3; i++ {
			valAddr, err := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			require.NoError(t, err)
			err = utils.ExecVoteInbound(t, ctx, chainApp, vals[i], sdk.AccAddress(valAddr).String(), inbound)
			require.NoError(t, err)
		}
		utxId := uexecutortypes.GetInboundUniversalTxKey(*inbound)

		log := buildRescueFundsLog(t, utxId, prc20Addr, senderAddr,
			"eip155", big.NewInt(111), big.NewInt(1_000_000_000), big.NewInt(200_000))
		err := chainApp.UexecutorKeeper.AttachRescueOutboundFromReceipt(ctx, makeRescueReceipt(t, "0xrescuetx03", log), uexecutortypes.PCTx{TxHash: "0xrescuetx03", Status: "SUCCESS"})
		require.Error(t, err)
		require.Contains(t, err.Error(), "no reverted inbound-revert outbound")
	})
```

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
