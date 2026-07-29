### Title
Attacker-supplied `fund_recipient` in `RevertInstructions` is never checked against the zero/burn address, permanently destroying refunded/reverted PRC20 funds - ([File: x/uexecutor/keeper/build_revert_outbound.go])

### Summary
An unprivileged sender who submits a cross-chain deposit can set `RevertInstructions.fund_recipient` to the EVM zero address (or any unspendable literal). When the inbound later fails validation/execution and a revert path fires, this attacker-controlled value is copied verbatim into the `INBOUND_REVERT` `OutboundTx.Recipient` with no zero-address (or "burn address") check anywhere in the pipeline, permanently destroying the user's own or (in mixed-flow scenarios) other bridged value.

### Finding Description
`RevertInstructions.fund_recipient` is a free-form string carried on the `Inbound` message and is fully attacker-controlled (it originates from the source-chain event data that becomes the `Inbound`) [1](#0-0) .

`Canonicalize()` only *lenient*-canonicalizes this field — if it doesn't parse as a valid namespaced address it is kept as trimmed input; it is never rejected, and a syntactically valid zero address (`0x000...000`) canonicalizes cleanly and passes straight through: [2](#0-1) 

When an inbound needs to be reverted, `buildRevertOutbound` uses `RevertInstructions.FundRecipient` directly as the outbound recipient with no additional validation: [3](#0-2) 

The only structural check performed anywhere downstream is `OutboundTx.ValidateBasic`, which merely requires the recipient string to be **non-empty** — it does not reject the zero address: [4](#0-3) 

A test explicitly documents that the revert outbound's recipient is taken verbatim from `RevertInstructions.FundRecipient` with no additional constraint: [5](#0-4) 

This is the direct structural analog of the Gearbox `_interestRateModel` report: a caller-supplied address field that feeds a critical protocol path (there, the interest model; here, the destination of refunded/burned bridge funds) with no zero-address guard.

### Impact Explanation
The `INBOUND_REVERT` outbound represents PRC20 that has already been minted/burned on Push Chain and is being sent back out to the source chain. If the recipient resolves to the zero address (a canonical "burn" address on virtually every EVM chain), the refunded principal is unrecoverably lost — the user (or, since `RevertInstructions` can diverge from `sender`, potentially funds intended for another party) suffers a permanent loss of protocol/bridge-held value. This matches the "permanent loss ... of user or protocol-controlled funds" allowed impact.

### Likelihood Explanation
Any external, unprivileged actor who initiates a cross-chain inbound event (a normal gateway deposit) fully controls the `revertInstructions.fundRecipient` field emitted in that event and can trivially set it to the zero address. No validator, TSS, or admin cooperation is required — the ballot/finalization machinery faithfully carries the value through once honest UVs reach quorum on the (identical, since it's part of the ballot digest) inbound. The only preconditions are that the inbound must actually take the revert path (e.g., missing token config, empty recipient, or another benign execution failure), which is easy for an attacker to force deliberately (e.g. by supplying an unregistered `asset_addr`).

### Recommendation
Add an explicit reject/normalize check for the zero address (and any other well-known burn addresses per chain, at minimum EVM's `0x000...0`) in:
- `Inbound.Canonicalize()` / a new `ValidateBasic`-style check on `RevertInstructions.FundRecipient`, and
- `buildRevertOutbound` (fallback to `inbound.Sender` when `FundRecipient` is the zero address, mirroring the existing empty-string fallback), and
- `OutboundTx.ValidateBasic`, extending the existing empty-check to also reject the zero address for EVM-style recipients.

### Proof of Concept
1. Attacker triggers a source-chain gateway deposit event with `TxType_FUNDS` (or `GAS`), a valid `Sender`, and `RevertInstructions.fund_recipient = "0x0000000000000000000000000000000000000000"`.
2. Attacker sets `asset_addr` to a token that is not registered in `uregistry` (or otherwise forces a benign, easily-triggered execution failure) so the honest UVs' vote quorum causes `ExecuteInboundFunds`/`ExecuteInboundGas` to fail and call `buildRevertOutbound`.
3. `buildRevertOutbound` copies `RevertInstructions.FundRecipient` (the zero address) into `OutboundTx.Recipient` unmodified (see [6](#0-5) ).
4. `OutboundTx.ValidateBasic` accepts it because the string is non-empty ( [4](#0-3) ).
5. TSS signs and the Universal Validators broadcast the outbound to the source chain, sending the reverted principal to the zero/burn address, permanently destroying it.

Note: I was not able to fully trace the TSS-signing/broadcast layer's own address validation (if any) for outbound recipients in the time available; if that layer independently filters the zero address before signing, the practical impact would be reduced to Push-Chain-side bookkeeping only. This should be verified with a live Devin session against `universalClient/tss/` broadcast/signing code before treating this as fully confirmed end-to-end.

### Citations

**File:** proto/uexecutor/v1/types.proto (L95-100)
```text
message RevertInstructions {
  option (amino.name) = "uexecutor/revert_instructions";
  option (gogoproto.equal) = true;

  string fund_recipient = 1;       // where funds go in revert/refund
}
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

**File:** x/uexecutor/keeper/build_revert_outbound.go (L10-25)
```go
func (k Keeper) buildRevertOutbound(sdkCtx sdk.Context, inbound *types.Inbound) *types.OutboundTx {
	recipient := inbound.Sender
	if inbound.RevertInstructions != nil && inbound.RevertInstructions.FundRecipient != "" {
		recipient = inbound.RevertInstructions.FundRecipient
	}

	outbound := &types.OutboundTx{
		DestinationChain:  inbound.SourceChain,
		Recipient:         recipient,
		Amount:            inbound.Amount,
		ExternalAssetAddr: inbound.AssetAddr,
		Sender:            inbound.Sender,
		TxType:            types.TxType_INBOUND_REVERT,
		OutboundStatus:    types.Status_PENDING,
		Id:                types.GetOutboundRevertId(inbound.SourceChain, inbound.TxHash, inbound.LogIndex),
	}
```

**File:** x/uexecutor/types/outbound_tx.go (L34-37)
```go
	// recipient must not be empty
	if strings.TrimSpace(p.Recipient) == "" {
		return errors.Wrap(sdkerrors.ErrInvalidAddress, "recipient cannot be empty")
	}
```

**File:** test/integration/uexecutor/execute_inbound_gas_test.go (L308-335)
```go
	t.Run("GAS inbound revert outbound uses FundRecipient from RevertInstructions", func(t *testing.T) {
		chainApp, ctx, vals, inbound, coreVals := setupInboundGasTest(t, 4)

		// Override revert instructions to a different recipient
		revertRecipient := utils.GetDefaultAddresses().TargetAddr2
		inbound.TxHash = "0xgas0010"
		inbound.RevertInstructions = &uexecutortypes.RevertInstructions{
			FundRecipient: revertRecipient,
		}

		reachGasQuorum(t, ctx, chainApp, vals, coreVals, inbound, 3)

		utxKey := uexecutortypes.GetInboundUniversalTxKey(*inbound)
		utx, found, err := chainApp.UexecutorKeeper.GetUniversalTx(ctx, utxKey)
		require.NoError(t, err)
		require.True(t, found)

		for _, ob := range utx.OutboundTx {
			if ob.TxType == uexecutortypes.TxType_INBOUND_REVERT {
				require.Equal(t, revertRecipient, ob.Recipient,
					"revert outbound recipient should match FundRecipient in RevertInstructions")
				return
			}
		}
		// If no revert outbound was created, the swap somehow succeeded — not expected
		// in the test environment, but skip rather than fail hard
		t.Skip("no INBOUND_REVERT found — swap may have unexpectedly succeeded in this environment")
	})
```
