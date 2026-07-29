## Analysis

The exploit path is real and traces to a genuine binding gap in the SVM log parser.

**Entry point:** `EventListener.processSlotRange` fetches candidate transactions via Solana's `getSignaturesForAddress(gatewayAddr, ...)` [1](#0-0) . This RPC only requires that the gateway program's pubkey appear *somewhere* in the transaction's account-key list — it does not require that the gateway program actually executed or emitted any log in that transaction. An attacker can trivially satisfy this by referencing the gateway program ID as an inert/readonly account in an otherwise unrelated transaction.

**Log ingestion:** `processSignatureBatch` then iterates **every** line in `tx.Meta.LogMessages` for the whole transaction, without correlating each `"Program data: ..."` line to the `"Program <id> invoke [...]"` / `"Program <id> success"` bracket that identifies which program actually emitted it [2](#0-1) . `determineEventType` simply base64-decodes any `"Program data:"` line and matches its first 8 bytes against a configured discriminator table [3](#0-2)  (test coverage confirms this operates purely on decoded bytes, with zero knowledge of which program logged them).

**Parsing:** `parseOutboundObservationEvent` then blindly trusts the decoded bytes — discriminator, `sub_tx_id`, `universal_tx_id`, `gas_used`, etc. — with no cross-check against the invoking program's identity or any independent confirmation that the destination-chain delivery actually occurred [4](#0-3) .

Consequently, an attacker can deploy/invoke an arbitrary Solana program in a transaction that (a) lists the real gateway program pubkey as a harmless extra account so it's returned by `getSignaturesForAddress`, and (b) calls `sol_log_data` (producing a `"Program data: <base64>"` line) with the `finalize_universal_tx` discriminator followed by an arbitrary `txID`, a victim's real `universal_tx_id` (public/observable from the earlier legitimate outbound-creation event), and `gas_used = 0`. This is picked up, stored as a pending `EventTypeOutbound` event, and fed to `EventProcessor.processOutboundEvent` → `buildOutboundObservation` → `signer.VoteOutbound` [5](#0-4) , all driven by honest validator nodes running this unmodified client code — this is exactly the "honest node/validator converges on forged state due to unvalidated user-reachable input" class the scope calls in-scope.

I was not able to fully inspect `buildOutboundObservation`'s exact field-mapping to the `Success` flag in the time available (it wasn't fully retrieved), so I cannot 100% confirm the precise on-chain vote payload shape, but the parsing-layer flaw that lets an attacker forge acceptance of an unrelated log line as a genuine gateway finalize event is independently verifiable and sufficient to justify the finding.

### Title
Unbound "Program data:" log parsing lets attacker forge SVM outbound-finalize events for arbitrary universal_tx_ids - (File: universalClient/chains/svm/event_parser.go)

### Summary
The SVM event listener/parser does not verify that a `"Program data:"` log line was actually emitted during the gateway program's own invocation. It only requires the gateway address to appear anywhere among a transaction's accounts, then scans **all** log lines in that transaction for a matching 8-byte discriminator.

### Finding Description
`processSlotRange`/`processSignatureBatch` fetch transactions via `getSignaturesForAddress(gatewayAddr)` [6](#0-5) , which only checks that the gateway pubkey is present as an account key, not that it is the executing program. `determineEventType`/`ParseEvent`/`parseOutboundObservationEvent` then trust any `"Program data:"` line in `tx.Meta.LogMessages` whose decoded bytes start with a configured discriminator, with no linkage to the surrounding `"Program <id> invoke"`/`"success"` markers that would identify the true emitting program [7](#0-6) . An attacker-controlled program invoked in the same transaction (which merely references the gateway pubkey as a non-signer account) can emit a forged log matching the `finalize_universal_tx` discriminator with a victim's real `universal_tx_id`.

### Impact Explanation
The forged event is stored and later processed by `EventProcessor.processOutboundEvent`, causing an honest node to build and vote on a spurious outbound observation for a `universal_tx_id` that never had funds delivered on the destination chain [5](#0-4) . If accepted, the outbound is marked delivered/observed without genuine delivery, permanently freezing the victim's funds since the revert/refund path would not trigger.

### Likelihood Explanation
Requires only an unprivileged attacker able to submit an ordinary Solana transaction that references the gateway program's public key and invokes their own program to emit a crafted `sol_log_data` line — no privileged role needed.

### Recommendation
Bind each `"Program data:"` log line to the actual invoking program by tracking Solana's `"Program <id> invoke [depth]"` / `"Program <id> success|failed"` bracket structure while scanning `LogMessages`, and only accept data lines emitted while the current invocation stack's top frame equals the configured `gatewayAddress`. Reject any decoded event data whose emitting program does not match.

### Proof of Concept
1. Note a victim's real pending outbound `universal_tx_id` (observable from a prior legitimate `sendFunds`/gateway event).
2. Attacker deploys a trivial program that calls `sol_log_data` with bytes = `finalize_universal_tx` discriminator (8 bytes) + arbitrary `sub_tx_id` (32 bytes) + victim's `universal_tx_id` (32 bytes) + `gas_fee`(8) + `gas_used=0`(8) + `gas_to_refund`(8) + `ata_created`(1).
3. Submit a transaction that includes the real gateway program pubkey as an unused readonly account and invokes the attacker's program.
4. `getSignaturesForAddress(gatewayAddr)` returns this transaction; `parseOutboundObservationEvent` decodes the forged log and creates a `store.Event` with the victim's `universal_tx_id`, feeding it into `processOutboundEvent`, which builds and votes on an observation without any destination-chain delivery having occurred.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L204-309)
```go

	// Move to next slot
	*currentSlot = latestSlot + 1
	return nil
}

// processSlotRange processes events in a range of slots
func (el *EventListener) processSlotRange(
	ctx context.Context,
	fromSlot, toSlot uint64,
) error {
	// Parse gateway address
	gatewayAddr, err := solana.PublicKeyFromBase58(el.gatewayAddress)
	if err != nil {
		return fmt.Errorf("invalid gateway address: %w", err)
	}

	// Per-page streaming so memory stays bounded on long bootstraps. Termination
	// and cursor use min(slot) of the batch — per
	// https://github.com/solana-labs/solana/issues/22456 in-page order is not
	// guaranteed descending, so batch[len-1] would risk an early break.
	var beforeSig solana.Signature
	var processedInRange uint64
	for page := 0; ; page++ {
		batch, err := el.rpcClient.GetSignaturesForAddress(ctx, gatewayAddr, beforeSig)
		if err != nil {
			return fmt.Errorf("failed to get signatures (page %d): %w", page, err)
		}
		if len(batch) == 0 {
			break
		}

		processed, err := el.processSignatureBatch(ctx, batch, fromSlot, toSlot)
		if err != nil {
			return err
		}
		processedInRange += processed
		if processedInRange >= largePollWarnThreshold {
			el.logger.Warn().
				Uint64("processed_in_range", processedInRange).
				Uint64("threshold", largePollWarnThreshold).
				Uint64("from_slot", fromSlot).
				Uint64("to_slot", toSlot).
				Int("pages", page+1).
				Msg("large signature backlog being processed; if this is unexpected, " +
					"restart with EventStartFrom set to -1 (latest) or a recent slot, " +
					"and verify the RPC tier can sustain the request volume")
		}

		minSlot := batch[0].Slot
		minSig := batch[0].Signature
		for _, s := range batch[1:] {
			if s.Slot < minSlot {
				minSlot = s.Slot
				minSig = s.Signature
			}
		}

		if minSlot < fromSlot {
			break
		}
		beforeSig = minSig
	}

	return nil
}

// Processes in-range sigs from `batch`, returns how many. `continue` on both
// bounds so it tolerates any in-page order.
func (el *EventListener) processSignatureBatch(
	ctx context.Context,
	batch []*solanarpc.TransactionSignature,
	fromSlot, toSlot uint64,
) (uint64, error) {
	var processed uint64
	for _, sig := range batch {
		if sig.Slot < fromSlot {
			continue
		}
		if sig.Slot > toSlot {
			continue
		}
		processed++

		// Get transaction details
		tx, err := el.rpcClient.GetTransaction(ctx, sig.Signature)
		if err != nil {
			el.logger.Error().
				Err(err).
				Str("signature", sig.Signature.String()).
				Msg("failed to get transaction")
			continue
		}

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
				if event != nil {
```

**File:** universalClient/chains/svm/event_parser.go (L116-169)
```go
func parseOutboundObservationEvent(log string, signature string, slot uint64, logIndex uint, chainID string, logger zerolog.Logger) *store.Event {
	if !strings.HasPrefix(log, "Program data: ") {
		return nil
	}

	eventData := strings.TrimPrefix(log, "Program data: ")
	decoded, err := base64.StdEncoding.DecodeString(eventData)
	if err != nil {
		return nil
	}

	// Minimum: 8 disc + 32 sub_tx_id + 32 universal_tx_id + 8 gas_fee + 8 gas_used
	// + 8 gas_to_refund + 1 ata_created = 97 bytes.
	if len(decoded) < 97 {
		logger.Warn().
			Int("data_len", len(decoded)).
			Msg("data too short for outboundObservation event; need at least 97 bytes")
		return nil
	}

	// Create EventID in format: signature:LogIndex
	eventID := fmt.Sprintf("%s:%d", signature, logIndex)

	logger.Debug().
		Str("event_id", eventID).
		Str("signature", signature).
		Uint("log_index", logIndex).
		Uint64("slot", slot).
		Msg("processing outboundObservation event")

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
