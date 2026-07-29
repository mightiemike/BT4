Confirmed: `event_confirmer.go` only checks `tx.Meta.Err == nil` and confirmation depth — it never re-validates which program actually emitted the "Program data:" log line. This confirms the finding below.

### Title
Unauthenticated gateway-event forgery via unscoped log scanning allows fake outbound/inbound observation events - (File: universalClient/chains/svm/event_parser.go, universalClient/chains/svm/event_listener.go)

### Summary
`parseOutboundObservationEvent` (and the sibling `parseSendFundsEvent`) never verify that the `"Program data: "` log line they parse was actually emitted by the configured gateway program. The listener selects candidate logs purely by string prefix and 8-byte Anchor discriminator match, scanning every log line of a transaction that merely references the gateway address.

### Finding Description
`EventListener.processSlotRange` fetches transactions solely via `GetSignaturesForAddress(gatewayAddr, ...)` [1](#0-0) , which returns any transaction where the gateway address appears among the account keys — not only transactions where the gateway program is actually invoked. `processSignatureBatch` then iterates over *all* `tx.Meta.LogMessages` of that transaction and calls `determineEventType`/`ParseEvent` on every line, with no association to which program (via "Program `<id>` invoke [depth]" / "success" framing) produced that log line [2](#0-1) .

`determineEventType` only checks the `"Program data: "` prefix and that the first 8 decoded bytes match a known Anchor event discriminator [3](#0-2) . Anchor event discriminators are `sha256("event:<Name>")[:8]` — a public, non-secret value computable by anyone, so they provide no authentication of the log's origin.

`parseOutboundObservationEvent` then blindly decodes the base64 payload from that log, only requiring a minimum length of 97 bytes, and extracts `txID`, `universalTxID`, and `gasUsed` (the only fields actually consumed) without ever confirming provenance from the real gateway program [4](#0-3) .

Because Solana composability allows arbitrary user-deployed programs to emit `msg!`/`sol_log_data`-style output that is indistinguishable in `LogMessages` from Anchor's `emit!` self-CPI "Program data:" lines, an unprivileged attacker can:
1. Deploy their own Solana program.
2. Submit one transaction that (a) references the gateway program's address as any account (read-only is enough to be indexed by `GetSignaturesForAddress`), and (b) invokes their own malicious program which logs a fabricated `"Program data: <base64>"` line whose first 8 bytes equal the `finalize_universal_tx`/`revert_universal_tx`/`funds_rescued` discriminator, followed by an attacker-chosen 32-byte `sub_tx_id`, 32-byte `universal_tx_id`, and `gas_used`, padded to ≥97 bytes.
3. This transaction succeeds on Solana (no error), so `EventConfirmer.processPendingEvents` will happily let it accrue confirmations and mark it `CONFIRMED` — it never checks that the log actually came from the gateway program, only that `tx.Meta.Err == nil` [5](#0-4) .

Critically, because the forged log is real, immutable, on-chain data, *every* honest Universal Validator node running this identical client code will independently parse the same forged event the same way and reach the same (wrong) conclusion — this does not require a malicious validator, relayer, or TSS signer. It is a pure client-side authentication gap exploitable by any unprivileged Solana user.

### Impact Explanation
An attacker can inject a forged `finalize_universal_tx` or `revert_universal_tx` outbound observation carrying an attacker-chosen `universal_tx_id`. If that ID collides with a real, still in-flight outbound UniversalTx tracked by Push Chain, honest UVs will all independently observe and vote to finalize/revert it prematurely based on fabricated data — potentially triggering unauthorized release, refund, or double-settlement of value tied to that UniversalTx, before the real outbound transfer has actually completed on Solana. The equivalent flaw in `parseSendFundsEvent`/`decodeUniversalTxEvent` (reachable via the identical unscoped log-scanning path in `determineEventType`) is even more severe, since a forged `send_funds` inbound event with attacker-controlled sender/recipient/amount/token could drive unauthorized minting on Push Chain.

### Likelihood Explanation
High. Exploitation only requires deploying a trivial Solana program and submitting one ordinary, unprivileged transaction; discriminators are public/derivable; no cryptographic secret or privileged role is needed. The only constraint is guessing/targeting a valid, colliding `universal_tx_id` for maximal impact, but even absent a targeted collision, the flaw lets an attacker inject arbitrary garbage events that pollute the pending-event pipeline and can be stored/voted on as legitimate observations.

### Recommendation
Do not trust raw `LogMessages` by content match alone. Parse the transaction's log structure to bind each `"Program data:"` line to the specific "Program `<gatewayProgramID>` invoke [...]" / "success" scope it was emitted within (or use `InnerInstructions`/CPI event parsing keyed off program ID, as Anchor's `EventParser` does), so only logs genuinely emitted via self-CPI by the configured gateway program address are accepted. Additionally, cross-validate parsed `universal_tx_id`/`sub_tx_id` against expected pending on-chain state before promoting an event to `CONFIRMED`.

### Proof of Concept
1. Compute the Anchor event discriminator for `FinalizeUniversalTx` (`sha256("event:FinalizeUniversalTx")[:8]`).
2. Deploy a minimal Solana program that, when invoked, does `sol_log_data` (or emits a raw `"Program data: <base64>"` log) with payload = `discriminator || attacker_sub_tx_id(32) || target_universal_tx_id(32) || gas_fee(8) || gas_used(8) || gas_to_refund(8) || ata_created(1)` (97 bytes, remaining optional fields omitted since they aren't parsed).
3. Build a single transaction that includes the gateway program's address as an account key (e.g., a harmless read of gateway state) plus a CPI/instruction to the attacker's program above.
4. Submit the transaction; it lands in `GetSignaturesForAddress(gatewayAddr)` results and its log lines are scanned indiscriminately by `processSignatureBatch`, `determineEventType` matches the discriminator, and `parseOutboundObservationEvent` accepts it as a genuine outbound observation for `target_universal_tx_id`.
5. After required confirmations, `EventConfirmer` marks it `CONFIRMED` purely based on `tx.Meta.Err == nil`, feeding a forged finalize/revert observation into the voting pipeline for a UniversalTx the attacker does not control.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L216-228)
```go
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
```

**File:** universalClient/chains/svm/event_listener.go (L298-328)
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
				if event != nil {
					// Insert event if it doesn't already exist
					if stored, err := el.chainStore.InsertEventIfNotExists(event); err != nil {
						el.logger.Error().
							Err(err).
							Str("event_id", event.EventID).
							Str("type", event.Type).
							Uint64("slot", event.BlockHeight).
							Msg("failed to store event")
					} else if stored {
						el.logger.Debug().
							Str("event_id", event.EventID).
							Str("type", event.Type).
							Uint64("slot", event.BlockHeight).
							Str("confirmation_type", event.ConfirmationType).
							Msg("stored new event")
					}
				}
			}
		}
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

**File:** universalClient/chains/svm/event_confirmer.go (L156-172)
```go
		// Check if transaction is confirmed
		if tx.Meta == nil {
			continue
		}

		// Solana preserves meta.logMessages even when meta.err is set, so a Program
		// data: line from a failed tx can reach the listener. Mark such events
		// REVERTED here so they never promote to CONFIRMED and trigger a vote.
		if tx.Meta.Err != nil {
			if _, updateErr := ec.chainStore.UpdateEventStatus(event.EventID, store.StatusPending, store.StatusReverted); updateErr != nil {
				ec.logger.Error().
					Err(updateErr).
					Str("event_id", event.EventID).
					Msg("failed to mark failed-tx event as REVERTED")
			}
			continue
		}
```
