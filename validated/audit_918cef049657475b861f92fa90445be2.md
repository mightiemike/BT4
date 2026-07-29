## Analysis

The vulnerability is real, but it is not really about the `signature:logIndex → payload` mapping (that mapping is preserved deterministically). The actual gap is upstream of `parseOutboundObservationEvent`, in how the SVM listener decides *which* logs are gateway events at all.

`EventListener.processSignatureBatch` fetches every transaction returned by `GetSignaturesForAddress(gatewayAddress)` and then iterates **every log line** in `tx.Meta.LogMessages`, calling `determineEventType(log)` on each one independently of which program emitted it: [1](#0-0) 

`determineEventType` only inspects the raw text of the log line (`"Program data: " + base64`) and matches the first 8 decoded bytes against a public discriminator table; it never checks that the log came from inside a `Program <gatewayAddress> invoke ... success` bracket: [2](#0-1) 

Solana's `getSignaturesForAddress` RPC returns any transaction where the address appears in `accountKeys`, whether or not that program was ever invoked. An unprivileged attacker can therefore build a transaction that (a) references the real gateway program pubkey as an inert account (satisfying the RPC filter) and (b) actually invokes an attacker-deployed program that calls `sol_log_data`/`emit!` with an 8-byte prefix equal to the (public, non-secret) Anchor discriminator for `finalize_universal_tx`, `revert_universal_tx`, or `funds_rescued`, followed by fully attacker-chosen bytes for `sub_tx_id`, `universal_tx_id`, and `gas_used`.

`parseOutboundObservationEvent` then blindly decodes those attacker bytes into `TxID`, `UniversalTxID`, and `GasFeeUsed` with no cross-check against the real gateway account/program: [3](#0-2) 

The resulting `store.Event` (`Type: EventTypeOutbound`) flows through `event_confirmer.go` and `EventProcessor.processOutboundEvent`, which builds an `OutboundObservation` straight from the forged `TxID`/`UniversalTxID`/`GasFeeUsed` and casts a vote: [4](#0-3) [5](#0-4) 

Since every honest UV's SVM listener independently decodes the same on-chain forged bytes deterministically, they converge on the same wrong observation — this is an "honest-validator convergence on a forged event" scenario, which is in-scope per the audit rules (no malicious validator/relayer assumption needed).

If the forged `UniversalTxID`/outbound id matches a real pending outbound (UTX/outbound identifiers are derivable/observable off-chain), `VoteOutbound` accepts it once quorum is reached because the only guard is `outbound.OutboundStatus != PENDING`: [6](#0-5) 

This flips a legitimate, not-yet-executed outbound straight to `OBSERVED`/`Success:true` with an attacker-chosen `GasFeeUsed`, permanently blocking the real observation (double-finalization guard) and triggering `applyGasRefund`, which computes `refundAmount = gasFee - gasFeeUsed` and calls `CallUniversalCoreRefundUnusedGas` to actually move funds to the outbound's sender/fund-recipient: [7](#0-6) 

By setting the forged `gas_used` artificially low, an attacker maximizes the "excess gas" refund paid out — a genuine, attacker-controlled fund transfer triggered from an ordinary Solana transaction, requiring no malicious validator, relayer, or admin.

### Title
Unauthenticated Solana log-provenance check lets attacker forge outbound observation events and steer honest UV votes - (File: universalClient/chains/svm/event_listener.go, universalClient/chains/svm/event_parser.go)

### Summary
`determineEventType`/`parseOutboundObservationEvent` classify Solana logs purely by matching an 8-byte discriminator embedded anywhere in `tx.Meta.LogMessages`, without verifying the log was emitted from within the real gateway program's `invoke`/`success` bracket. Because `GetSignaturesForAddress` returns any transaction merely referencing the gateway pubkey (not necessarily invoking it), and because Anchor event discriminators are public, an unprivileged attacker can get an arbitrary attacker-controlled program to emit a fake `Program data:` log that is indistinguishable to the listener from a genuine gateway `finalize_universal_tx`/`revert_universal_tx`/`funds_rescued` event, with fully attacker-chosen `sub_tx_id`, `universal_tx_id`, and `gas_used` payload bytes.

### Finding Description
See analysis above: missing program-provenance binding in `determineEventType`/`processSignatureBatch` and `parseOutboundObservationEvent` allows arbitrary log-based data injection into the outbound-observation pipeline, which honest UVs then vote on identically, reaching quorum honestly on forged data.

### Impact Explanation
Forged outbound observations can (1) prematurely mark a real pending outbound as `OBSERVED`, permanently blocking its legitimate finalization, and (2) drive `applyGasRefund` to transfer attacker-influenced "excess gas" amounts via `CallUniversalCoreRefundUnusedGas`, i.e., unauthorized value movement/refund corruption reachable by an ordinary unprivileged Solana transaction.

### Likelihood Explanation
Requires only submitting one Solana transaction that references the gateway pubkey and invokes an attacker-controlled program emitting a crafted log — no privileged role, validator collusion, or gateway compromise needed. The main uncertainty (not fully verifiable from the index) is how easily an attacker can learn/predict a target UTX's exact 32-byte `universal_tx_id`/outbound id bytes in the exact format the listener expects; this needs confirmation but the underlying provenance-validation gap itself is unconditional.

### Recommendation
Bind log classification to the actual invoking program: parse the `Program <id> invoke [depth]` / `Program <id> success` bracket structure in `tx.Meta.LogMessages` and only classify `Program data:` lines that fall within the gateway program's own invocation frame as gateway events, rather than scanning raw log text/discriminator bytes irrespective of origin.

### Proof of Concept
1. Deploy an arbitrary Solana program that calls `sol_log_data` with payload `discriminator(finalize_universal_tx) || sub_tx_id || universal_tx_id || gas_fee || gas_used || ...` matching the 97+ byte layout expected by `parseOutboundObservationEvent`.
2. Submit a transaction that includes the real gateway program pubkey as a read-only account and invokes the attacker program to emit the crafted log.
3. Observe that `EventListener.processSignatureBatch` (via `GetSignaturesForAddress(gatewayAddress)`) picks up this transaction, `determineEventType` matches the discriminator, and `parseOutboundObservationEvent` produces a `store.Event` with attacker-chosen `TxID`/`UniversalTxID`/`GasFeeUsed`.
4. Confirm this event proceeds through `EventProcessor.processOutboundEvent` → `VoteOutbound`, and, if the forged `UniversalTxID` matches a real pending outbound, `FinalizeOutbound`/`applyGasRefund` triggers a fund transfer based on the attacker-chosen `gas_used`.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L298-308)
```go
		// Process each log in the transaction
		if tx != nil && tx.Meta != nil && len(tx.Meta.LogMessages) > 0 {
			for logIndex, log := range tx.Meta.LogMessages {
				// Determine event type based on discriminator
				eventType := el.determineEventType(log)
				if eventType == "" {
					continue
				}

				// Parse gateway event from individual log
				event := ParseEvent(log, sig.Signature.String(), sig.Slot, uint(logIndex), eventType, el.chainID, el.logger)
```

**File:** universalClient/chains/svm/event_listener.go (L398-423)
```go
// determineEventType determines the event type based on the log discriminator
func (el *EventListener) determineEventType(log string) string {
	if !strings.HasPrefix(log, "Program data: ") {
		return ""
	}

	eventData := strings.TrimPrefix(log, "Program data: ")
	decoded, err := base64.StdEncoding.DecodeString(eventData)
	if err != nil {
		return ""
	}

	if len(decoded) < 8 {
		return ""
	}

	discriminator := strings.ToLower(hex.EncodeToString(decoded[:8]))

	// Look up event type from discriminator map
	eventType, ok := el.discriminatorToEventType[discriminator]
	if !ok {
		return ""
	}

	return eventType
}
```

**File:** universalClient/chains/svm/event_parser.go (L146-169)
```go
	// Skip discriminator (8 bytes)
	offset := 8

	// Extract txID (32 bytes)
	txID := "0x" + hex.EncodeToString(decoded[offset:offset+32])
	offset += 32

	// Extract universalTxID (32 bytes)
	universalTxID := "0x" + hex.EncodeToString(decoded[offset:offset+32])
	offset += 32

	// Skip gas_fee (prepaid budget, 8 bytes); the audited finalize event reports
	// gas_used separately and that's the value we want to surface as GasFeeUsed.
	offset += 8

	// Extract gas_used (8 bytes, u64 little-endian lamports) — actual gas consumed.
	gasUsed := binary.LittleEndian.Uint64(decoded[offset : offset+8])

	// Create OutboundEvent payload
	payload := common.OutboundEvent{
		TxID:          txID,
		UniversalTxID: universalTxID,
		GasFeeUsed:    fmt.Sprintf("%d", gasUsed),
	}
```

**File:** universalClient/chains/common/event_processor.go (L152-177)
```go
// processOutboundEvent processes an outbound event by voting on it
func (ep *EventProcessor) processOutboundEvent(ctx context.Context, event *store.Event) error {
	ep.logger.Debug().
		Str("event_id", event.EventID).
		Msg("processing outbound event")

	// Parse outbound event data once
	outboundData, err := ep.parseOutboundEventData(event)
	if err != nil {
		return fmt.Errorf("failed to parse outbound event data: %w", err)
	}

	txID := outboundData.TxID
	utxID := outboundData.UniversalTxID

	// Build observation from parsed data
	observation, err := ep.buildOutboundObservation(event, outboundData)
	if err != nil {
		return fmt.Errorf("failed to build outbound observation: %w", err)
	}

	// Vote on outbound
	voteTxHash, err := ep.signer.VoteOutbound(ctx, txID, utxID, observation)
	if err != nil {
		return fmt.Errorf("failed to vote on outbound: %w", err)
	}
```

**File:** universalClient/chains/common/event_processor.go (L364-397)
```go
// buildOutboundObservation builds an OutboundObservation from event metadata and parsed outbound data
func (ep *EventProcessor) buildOutboundObservation(event *store.Event, outboundData *OutboundEvent) (*uexecutortypes.OutboundObservation, error) {
	// Extract txHash from EventID (format: "txHash:logIndex" or "signature:logIndex")
	txHash := ""
	parts := strings.Split(event.EventID, ":")
	if len(parts) > 0 {
		txHash = parts[0]
	}

	// Convert txHash to hex format if it's in base58
	txHashHex, err := ep.base58ToHex(txHash)
	if err != nil {
		ep.logger.Warn().
			Str("tx_hash", txHash).
			Err(err).
			Msg("failed to convert txHash to hex, using original value")
		txHashHex = txHash
	}

	gasFeeUsed := "0"
	if outboundData.GasFeeUsed != "" {
		gasFeeUsed = outboundData.GasFeeUsed
	}

	observation := &uexecutortypes.OutboundObservation{
		Success:     true,
		BlockHeight: event.BlockHeight,
		TxHash:      txHashHex,
		ErrorMsg:    "",
		GasFeeUsed:  gasFeeUsed,
	}

	return observation, nil
}
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L62-69)
```go
	// Prevent double-finalization
	if outbound.OutboundStatus != types.Status_PENDING {
		k.Logger().Warn("vote outbound rejected: outbound already finalized",
			"outbound_id", outboundId,
			"status", outbound.OutboundStatus.String(),
		)
		return fmt.Errorf("outbound with key %s is already finalized", outboundId)
	}
```

**File:** x/uexecutor/keeper/outbound.go (L163-230)
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
```
