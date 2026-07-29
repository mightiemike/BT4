This is a critical finding: `BuildOutboundsFromReceipt` in `x/uexecutor/keeper/create_outbound.go` derives `RevertInstructions.FundRecipient` directly from an EVM log (`event.RevertRecipient`) emitted by `UniversalGatewayPC` during execution of a user-submitted payload — not from a consensus-verified, ballot-keyed field like the inbound-side `RevertInstructions`. [1](#0-0) 

### Title
Attacker-controlled `RevertRecipient` in outbound-creation event diverts gas-refund surplus, unbound from any voted/consensus digest - (File: x/uexecutor/keeper/create_outbound.go)

### Summary
`BuildOutboundsFromReceipt` sets `OutboundTx.RevertInstructions.FundRecipient` straight from the `UniversalTxOutboundEvent` log emitted during EVM execution of a user's `MsgExecutePayload` / `executeUniversalTx` call, with no validation that this recipient corresponds to the payload's rightful owner or destination. [2](#0-1)  This value later becomes the recipient of both a failed-outbound fund reversal and, more importantly, of any refunded excess gas fee (`gasFee - gasFeeUsed`) via `applyGasRefund`. [3](#0-2) 

### Finding Description
This is the closest structural analog to the CoW `appData`-ignored-by-`isValidSignature` bug class: a field that determines where "surplus" value is routed is *not* bound by the authorization/consensus mechanism that gates the rest of the transaction.

- For **inbound-side** reverts, `RevertInstructions.FundRecipient` is part of `GetInboundBallotKey`, so honest Universal Validators must reach quorum on that exact value before it is trusted. [4](#0-3) 
- For **outbound-side** flows (the reverse direction — Push Chain executing a user's payload that triggers a withdrawal to an external chain), the analogous `RevertInstructions.FundRecipient` is instead read directly off an EVM event emitted during payload execution — `event.RevertRecipient` — with no cross-check against the UEA owner, the `MsgExecutePayload.UniversalAccountId.Owner`, or any other pre-authorized value. [1](#0-0) 
- The `MsgExecutePayload` authorization model already explicitly allows `Signer != Owner` (any relayer can submit) and the only binding to the true owner is the `verificationData` signature checked inside the UEA contract (out of this repo's scope) against `to`, `value`, `data`, `gasLimit`, fee params, `nonce`, `deadline` — but *not* the outbound's `revertRecipient` field emitted by the smart-contract's own logic. [5](#0-4) 
- `applyGasRefund` (run on both successful and failed outbound resolution, unconditionally, regardless of tx type) sends the *excess gas fee* — the "surplus" analogous to CoW's leftover trade value — to exactly this `RevertInstructions.FundRecipient`, falling back to `outbound.Sender` only when it is empty. [6](#0-5) 

The chain module trusts whatever a contract call inside the executed payload chooses to emit as `revertRecipient`, and forwards that value into surplus-bearing refund logic without any secondary authorization check tying it back to the payload owner. If the contracts on the EVM side (`UniversalGatewayPC` / `UEA`) do not themselves enforce that `revertRecipient` can only be set by the owner or matches an owner-approved value at the time the payload was signed, this Cosmos-layer code path becomes the same "arbitrary auxiliary field diverts surplus" pattern described in the CoW report.

### Impact Explanation
If the EVM-side contract emitting this event allows an unprivileged caller (or a malicious/compromised relayer crafting calldata within the payload) to set `revertRecipient` to an address they control, they can redirect: (1) reverted bridged funds on a failed outbound, and (2) excess/unused gas-fee refunds on both successful and failed outbounds — both of which rightfully belong to the UEA owner. This is a fund-diversion vector matching the "unauthorized release/refund of user-controlled funds" category in scope.

### Likelihood Explanation
Low-to-Medium and **unverifiable from this repository alone**. The actual authorization gate for `revertRecipient` lives in the EVM contract (`UniversalGatewayPC`/UEA) that emits `UniversalTxOutboundEvent`, which is outside this repo's index. If that contract restricts `revertRecipient` to the caller/owner at the time of the signed payload (analogous to CoW's suggested fix of binding app data), there is no exploitable path from this repo's code alone. This finding should be treated as a pointer to verify contract-side binding of `revertRecipient`, not a confirmed standalone vulnerability in `push-chain-node`.

### Recommendation
- Cross-check `event.RevertRecipient` in `BuildOutboundsFromReceipt` against the payload's authenticated owner (`UniversalAccountId.Owner` / UEA identity) before trusting it for refund routing, mirroring how `GetInboundBallotKey` binds `FundRecipient` into the consensus-verified digest on the inbound side.
- Alternatively/additionally, verify (or require Devin session access to) the `UniversalGatewayPC` and UEA Solidity contracts to confirm `revertRecipient` cannot be set independently of the signed `UniversalPayload`/`verificationData`.

### Proof of Concept
Not constructible from this repository alone — the trigger point (whether an attacker can set an arbitrary `revertRecipient` in the emitted event) lives in the external `push-chain-core-contracts` repository (`UniversalGatewayPC`, `UEA_EVM.sol`), which is not indexed here. A background Devin session with contract-repo access would be needed to confirm whether `revertRecipient` is bound to the signed payload or attacker-settable, and thus whether this is exploitable end-to-end.

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

**File:** x/uexecutor/keeper/outbound.go (L163-206)
```go
// handleSuccessfulOutbound refunds unused gas fee when gasFee > gasFeeUsed.
func (k Keeper) handleSuccessfulOutbound(ctx sdk.Context, utxId string, outbound types.OutboundTx, obs *types.OutboundObservation) error {
	k.Logger().Info("outbound completed successfully",
		"utx_id", utxId,
		"outbound_id", outbound.Id,
		"dest_chain", outbound.DestinationChain,
	)
	k.applyGasRefund(ctx, &outbound, obs)
	return k.UpdateOutbound(ctx, utxId, outbound)
}

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
```

**File:** x/uexecutor/types/keys.go (L99-125)
```go
func GetInboundBallotKey(inbound Inbound) (string, error) {
	chain := strings.TrimSpace(inbound.SourceChain)

	// nil RevertInstructions and an empty FundRecipient are semantically
	// identical (revert falls back to sender) — digest them identically.
	fundRecipient := ""
	if inbound.RevertInstructions != nil {
		fundRecipient = utils.LenientCanonicalizeAddress(chain, inbound.RevertInstructions.FundRecipient)
	}

	return hashFields(
		InboundBallotDomain,
		chain,
		utils.LenientCanonicalizeTxHash(chain, inbound.TxHash),
		strings.TrimSpace(inbound.LogIndex),
		utils.LenientCanonicalizeAddress(chain, inbound.Sender),
		// Recipient lives on Push Chain (EVM) regardless of source chain.
		utils.LenientCanonicalizeEVMAddress(inbound.Recipient),
		strings.TrimSpace(inbound.Amount),
		utils.LenientCanonicalizeAddress(chain, inbound.AssetAddr),
		fmt.Sprintf("%d", inbound.TxType),
		utils.CanonicalizeHexBlob(inbound.VerificationData),
		fundRecipient,
		fmt.Sprintf("%t", inbound.IsCEA),
		utils.CanonicalizeHexBlob(inbound.RawPayload),
		// universal_payload intentionally excluded (derived, ignored on-chain).
	), nil
```

**File:** x/uexecutor/README.md (L224-227)
```markdown
1. The contract holds the owner's public key as **immutable bytes** set at UEA deployment via `initialize(_id, _factory)`. There is no code path that mutates this after init.
2. `executeUniversalTx(payload, signature)` verifies the `signature` (passed in as `MsgExecutePayload.VerificationData`) against this stored owner — ECDSA recovery for EVM-origin owners, the Ed25519 precompile (`0x00…00ca`) for SVM-origin owners.
3. The signed payload hash includes a contract-tracked `nonce` (monotonic per UEA) and optional `deadline`, providing replay and freshness protection.
4. If signature verification fails, the contract reverts. The revert propagates as `execErr` from `CallUEAExecutePayload`; the keeper returns the error from `ExecutePayload`; the entire Cosmos transaction (including any partial gas-fee deduction) rolls back atomically. **No state changes survive a failed signature check.**
```
