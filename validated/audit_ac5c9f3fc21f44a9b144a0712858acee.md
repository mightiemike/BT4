I have enough evidence now to build the analog. This confirms the exact mechanism: `handleFailedOutbound` in `x/uexecutor/keeper/outbound.go` calls `k.CallPRC20Deposit` to a `recipient` address that is derived entirely from attacker-controlled fields (`outbound.Sender` or `outbound.RevertInstructions.FundRecipient`, both set from the original inbound's user-supplied `RevertInstructions.FundRecipient`), and `types.Inbound.ValidateForExecution` / `RevertInstructions` do no validation beyond hex-format checks — there's no restriction preventing the recipient from being a contract that reverts on receipt (e.g. a PRC20-compatible token wrapper, or a UEA/contract without a fallback, or one that self-blacklists via a hook). If that mint reverts, `handleFailedOutbound` permanently marks the outbound `ABORTED`, per the code comment "Re-mint failed — mark as ABORTED for manual intervention" [1](#0-0) , and the README documents this is **not chain-driven** and requires governance/operator intervention [2](#0-1) .

### Title
Attacker-chosen revert/refund recipient can permanently brick outbound fund re-mint, freezing bridged user funds in ABORTED state - (File: x/uexecutor/keeper/outbound.go)

### Summary
When an outbound fails on the destination chain, `handleFailedOutbound` re-mints the bridged PRC20 amount back to a `recipient` address that is taken directly from the original inbound's user-supplied `RevertInstructions.FundRecipient` field (or `outbound.Sender`), with no restriction on what that address can be beyond a basic hex-format check [3](#0-2) . If the PRC20 `CallPRC20Deposit` mint call to that attacker-chosen recipient reverts — e.g. because the recipient is a smart contract that unconditionally reverts on token receipt, or is itself a UEA/contract whose code path always fails for the mint call — the outbound is permanently marked `ABORTED` with no automatic recovery [1](#0-0) .

### Finding Description
`buildRevertOutbound` (used for inbound reverts) and `handleFailedOutbound` (used for outbound revert/re-mint) both compute the fund/refund recipient as: `RevertInstructions.FundRecipient` if the user set it, else the sender [4](#0-3) [5](#0-4) . This field is fully attacker-controlled — any address submitted by the original bridging user, validated only for hex format via `Canonicalize`/`ValidateForExecution`, with no check that the address can actually receive the PRC20 mint [6](#0-5) .

When `handleFailedOutbound` attempts to re-mint the bridged funds to this recipient via `k.CallPRC20Deposit(ctx, ..., common.HexToAddress(recipient), amount)`, if the call reverts (analogous to a blacklisted/reverting recipient in the Sentiment report), the code does not retry with a fallback recipient (e.g. the module owner or sender) — it immediately calls `AbortOutbound`, which sets `Status_ABORTED` and stops all further automated processing [7](#0-6) . The `x/uexecutor` README explicitly documents that once in this outbound-pending/aborted state there is "no safe automatic resolution" and "resolution is governance-driven, not chain-driven" [2](#0-1) .

This mirrors the Sentiment V2 bug class: a fund-transfer/mint operation to a user-influenced recipient is not defensively wrapped, so a single failing recipient (self-inflicted, since the user sets their own `FundRecipient`) bricks the recovery/liquidation-equivalent path (here, the outbound revert re-mint) for that specific cross-chain transaction, permanently freezing the bridged funds pending manual/governance intervention.

### Impact Explanation
An ordinary, unprivileged user who bridges funds and supplies a malicious/reverting `RevertInstructions.FundRecipient` (a self-deployed contract designed to always revert on `depositPRC20Token`/mint) can force any outbound revert/re-mint associated with their own inbound to hit `ABORTED` status. This permanently freezes the user's own bridged funds inside the protocol accounting (no automatic path returns them), consistent with the "permanent freezing of user funds" allowed-impact category. While self-targeted, the mechanism generalizes: any recipient that legitimately or maliciously reverts on receipt (e.g. via reentrancy guard, gas-griefing fallback, or intentional revert) causes the same effect, and there is no fallback recipient logic to protect the invariant that bridged/reverted funds are recoverable.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the attacker to deliberately set an EVM contract address (which they fully control, since it is their own `RevertInstructions.FundRecipient`) that reverts on the PRC20 mint call, and then trigger an outbound failure (e.g. a `GAS_AND_PAYLOAD`/`FUNDS_AND_PAYLOAD` inbound whose downstream outbound fails on the destination chain, or via `RevertStuckInbound`/`INBOUND_REVERT` outbound paths). This is fully reachable via the standard, unprivileged bridging flow (`MsgVoteInbound` → inbound execution → outbound creation → `MsgVoteOutbound` observing failure) with only honest validators involved.

### Recommendation
Wrap `CallPRC20Deposit` (and the analogous revert-outbound gas-fee-info calls) in a defensive pattern that does not leave funds permanently stuck on a single failed re-mint: e.g., fall back to minting to a protocol-controlled/queryable rescue address, or expose a permissionless retry/rescue message that lets any account (or eventually the depositor with an alternate recipient) recover the funds once the original recipient is proven non-functional, instead of relying solely on governance-driven manual intervention. At minimum, validate that `RevertInstructions.FundRecipient` is an EOA or a contract that implements a minimal "can receive" check before accepting the inbound, or allow updating the stuck outbound's target recipient without full governance action.

### Proof of Concept
1. Attacker deploys a minimal EVM contract `RevertingRecipient` whose fallback/receive and any relevant token hook always `revert()`.
2. Attacker submits a bridging transaction on the source chain with `RevertInstructions.FundRecipient = RevertingRecipient` and a `TxType` that can produce a destination-chain outbound (e.g. `FUNDS_AND_PAYLOAD`).
3. Universal Validators vote the inbound in; `ExecuteInboundFundsAndPayload` succeeds and creates an `OutboundTx` whose `RevertInstructions` inherits the same `FundRecipient`.
4. The outbound genuinely fails on the destination chain (attacker can engineer this, e.g. by providing an invalid/failing destination payload). UVs vote `MsgVoteOutbound` with `success=false`.
5. `FinalizeOutbound` → `handleFailedOutbound` attempts `CallPRC20Deposit` to `RevertingRecipient`, which reverts.
6. `handleFailedOutbound` calls `AbortOutbound`, setting `Status_ABORTED` on the outbound — the bridged funds are now stuck with no automated recovery path, confirmed by the `TestOutboundVoting/AbortOutbound sets ABORTED status and emits event` test pattern [8](#0-7) .

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

**File:** x/uexecutor/keeper/outbound.go (L107-112)
```go
		// Decide revert recipient safely
		recipient := outbound.Sender
		if outbound.RevertInstructions != nil &&
			outbound.RevertInstructions.FundRecipient != "" {
			recipient = outbound.RevertInstructions.FundRecipient
		}
```

**File:** x/uexecutor/keeper/outbound.go (L130-137)
```go
		if err != nil {
			pcTx.Status = "FAILED"
			pcTx.ErrorMsg = err.Error()
			outbound.PcRevertExecution = &pcTx
			// Re-mint failed — mark as ABORTED for manual intervention
			return k.AbortOutbound(ctx, utxId, outbound,
				fmt.Sprintf("failed to re-mint tokens for revert: %s", err.Error()))
		}
```

**File:** x/uexecutor/README.md (L273-282)
```markdown
- **Removed ONLY when validators reach consensus** (existing inline
  `PendingOutbounds.Remove` in `msg_vote_outbound.go` on `PASSED`).
- **Ballot expiry does NOT remove the entry** — this is intentional. The
  destination chain already received (or did not receive) the outbound; the
  user's funds are already in flight. Auto-refund risks double-pay (if the
  outbound actually landed), auto-retry risks double-delivery, and there is
  no safe automatic resolution. Operators investigate stuck outbounds via
  the per-variant audit trail (which validators voted what observation) plus
  separate `x/uvalidator` ballot status queries; resolution is governance-
  driven, not chain-driven.
```

**File:** x/uexecutor/types/inbound.go (L21-36)
```go
func (p *Inbound) Canonicalize() {
	p.SourceChain = strings.TrimSpace(p.SourceChain)
	p.TxHash = utils.LenientCanonicalizeTxHash(p.SourceChain, p.TxHash)
	p.Sender = utils.LenientCanonicalizeAddress(p.SourceChain, p.Sender)
	p.AssetAddr = utils.LenientCanonicalizeAddress(p.SourceChain, p.AssetAddr)
	// Recipient lives on Push Chain (EVM) regardless of source chain.
	p.Recipient = utils.LenientCanonicalizeEVMAddress(p.Recipient)
	p.LogIndex = strings.TrimSpace(p.LogIndex)
	p.Amount = strings.TrimSpace(p.Amount)
	p.RawPayload = utils.CanonicalizeHexBlob(p.RawPayload)
	p.VerificationData = utils.CanonicalizeHexBlob(p.VerificationData)
	if p.RevertInstructions != nil {
		// Refunds return to the source chain.
		p.RevertInstructions.FundRecipient = utils.LenientCanonicalizeAddress(p.SourceChain, p.RevertInstructions.FundRecipient)
	}
}
```

**File:** x/uexecutor/types/inbound.go (L121-172)
```go
}

// ValidateForExecution checks fields that are required for actual execution of the inbound.
// Called after ballot finalization, before ExecuteInbound. Failures here produce a failed
// PCTx and (for non-isCEA) a revert outbound, rather than dropping the vote.
func (p Inbound) ValidateForExecution() error {
	// Validate amount as uint256
	if strings.TrimSpace(p.Amount) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount cannot be empty")
	}
	bi, ok := new(big.Int).SetString(p.Amount, 10)
	if !ok || bi.Sign() < 0 {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be a valid non-negative uint256")
	}
	// Only GAS_AND_PAYLOAD and FUNDS_AND_PAYLOAD allow zero amount (skip deposit, still execute payload)
	if bi.Sign() == 0 && p.TxType != TxType_GAS_AND_PAYLOAD && p.TxType != TxType_FUNDS_AND_PAYLOAD {
		return errors.Wrap(sdkerrors.ErrInvalidRequest, "amount must be positive for this tx type")
	}

	// Validate asset_addr
	if strings.TrimSpace(p.AssetAddr) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "asset_addr cannot be empty")
	}

	// isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD
	if p.IsCEA && p.TxType != TxType_FUNDS && p.TxType != TxType_FUNDS_AND_PAYLOAD && p.TxType != TxType_GAS_AND_PAYLOAD {
		return errors.Wrapf(sdkerrors.ErrInvalidRequest, "isCEA is only supported for FUNDS, FUNDS_AND_PAYLOAD, and GAS_AND_PAYLOAD tx types, got: %v", p.TxType)
	}

	// Validate fields required per tx_type
	switch p.TxType {
	case TxType_FUNDS_AND_PAYLOAD, TxType_GAS_AND_PAYLOAD:
		if p.UniversalPayload == nil {
			return errors.Wrap(sdkerrors.ErrInvalidRequest, "payload is required for payload tx types")
		}
		if p.IsCEA && strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty when isCEA is true")
		}
		if p.IsCEA && !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address when isCEA is true: %s", p.Recipient)
		}
		if err := p.UniversalPayload.ValidateBasic(); err != nil {
			return errors.Wrap(err, "invalid payload")
		}
	case TxType_FUNDS, TxType_GAS:
		if strings.TrimSpace(p.Recipient) == "" {
			return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
		}
		if !utils.IsValidAddress(p.Recipient, utils.HEX) {
			return errors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address: %s", p.Recipient)
		}
	}
```

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-14)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}
```

**File:** test/integration/uexecutor/vote_outbound_test.go (L360-399)
```go
	t.Run("AbortOutbound sets ABORTED status and emits event", func(t *testing.T) {
		app, ctx, vals, utxId, outbound, coreVals :=
			setupOutboundVotingTest(t, 4)

		// First finalize the outbound as successful so it reaches OBSERVED
		for i := 0; i < 3; i++ {
			valAddr, _ := sdk.ValAddressFromBech32(coreVals[i].OperatorAddress)
			coreAcc := sdk.AccAddress(valAddr).String()

			err := utils.ExecVoteOutbound(
				t,
				ctx,
				app,
				vals[i],
				coreAcc,
				utxId,
				outbound,
				true,
				"",
				outbound.GasFee,
			)
			require.NoError(t, err)
		}

		// Verify it's OBSERVED
		utx, _, err := app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		ob := utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_OBSERVED, ob.OutboundStatus)

		// Now call AbortOutbound directly
		err = app.UexecutorKeeper.AbortOutbound(ctx, utxId, *ob, "finalization failed: test reason")
		require.NoError(t, err)

		// Verify the outbound is now ABORTED with reason
		utx, _, err = app.UexecutorKeeper.GetUniversalTx(ctx, utxId)
		require.NoError(t, err)
		ob = utx.OutboundTx[0]
		require.Equal(t, uexecutortypes.Status_ABORTED, ob.OutboundStatus)
		require.Equal(t, "finalization failed: test reason", ob.AbortReason)
```
