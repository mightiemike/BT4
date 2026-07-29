## Finding

Confirmed by reading `universalClient/chains/svm/event_listener.go` and `universalClient/chains/svm/event_parser.go`: the SVM gateway-event pipeline authenticates events purely by **discriminator bytes found anywhere in a transaction's flat log array**, with no verification that the log line was actually emitted from inside the gateway program's own CPI invocation frame.

### Title
Unauthenticated log-source binding lets an unprivileged actor forge SVM gateway events (inbound `send_funds` and outbound `finalize_universal_tx`/`revert_universal_tx`/`funds_rescued`) - (File: `universalClient/chains/svm/event_listener.go`, `universalClient/chains/svm/event_parser.go`)

### Summary
`processSignatureBatch` fetches transactions via `GetSignaturesForAddress(gatewayAddr, ...)`, which Solana RPC indexes by *any* account reference in a transaction's static account-keys list — not by actual program invocation. It then iterates the **entire** `tx.Meta.LogMessages` array with `determineEventType(log)` and `ParseEvent(...)`, matching only on the raw text prefix `"Program data: "` and the leading 8-byte Anchor discriminator: [1](#0-0) 

`determineEventType` performs no check on which program emitted the log (no `"Program <gatewayAddress> invoke"`/`"... success"` bracket matching), and Anchor event discriminators (`sha256("event:<Name>")[:8]`) are public/computable from the gateway's own registered `EventIdentifier` config: [2](#0-1) 

`parseOutboundObservationEvent` then blindly decodes whatever bytes follow that discriminator as `sub_tx_id`, `universal_tx_id`, and `gas_used`, with zero binding to the real gateway account or PDA: [3](#0-2) 

The parallel inbound path (`parseSendFundsEvent` → `decodeUniversalTxEvent`) is equally unauthenticated, decoding attacker-chosen `sender`, `recipient`, `token`, `amount`, and `payload` bytes verbatim: [4](#0-3) 

### Finding Description
An unprivileged attacker can:
1. Deploy an arbitrary, self-owned Solana program (permissionless).
2. In a single transaction, include the real gateway program's address as an unused/read-only account key (legal in Solana; the account-keys list of a transaction need not be exclusively used by an invoked instruction) so the transaction becomes indexed by `GetSignaturesForAddress(gatewayAddr, ...)`.
3. Have their own program emit `sol_log_data(...)` producing a `"Program data: <base64>"` log line whose first 8 bytes exactly match the gateway's public Anchor discriminator for `finalize_universal_tx`, `revert_universal_tx`, `funds_rescued`, or `send_funds`, followed by fully attacker-chosen payload bytes (arbitrary `sub_tx_id`, `universal_tx_id`, `gas_used`, `sender`, `recipient`, `token`, `amount`, `payload`).

Because `processSignatureBatch` scans every log line in the transaction without validating that it was emitted from within an actual invocation of the gateway program, this forged log is parsed exactly like a genuine gateway emission and stored as a `store.Event` with `Status: Pending`. Since this happens against the real Solana chain state (the forged log literally exists on-chain once the transaction lands), **every honest UV node** running this same listener code will independently observe and parse the identical forged event and converge on the same fabricated `EventID`/payload — this is not a "malicious validator" scenario, it is honest nodes being fed poisoned but genuinely-on-chain input, which the review's honest-validator finalization pivot explicitly puts in scope.

This lets the attacker:
- Forge a `finalize_universal_tx` or `revert_universal_tx` observation carrying an arbitrary (attacker-chosen or guessed real, since `universal_tx_id` values are not secret) `universal_tx_id`, pushing an unrelated or attacker-fabricated outbound to terminal vote state without the real gateway ever executing the finalize/revert instruction.
- Forge a `send_funds` inbound event with arbitrary sender/recipient/token/amount, potentially causing crediting/execution flows keyed off attacker-controlled fields.

This directly violates the stated invariant that "wrong-type, malformed, or replayed SVM logs never reach terminal vote state."

### Impact Explanation
If the downstream voting/finalization module (outside the files reviewed here, in `x/`) trusts the `store.Event` payload produced by this listener without independently re-verifying that the log actually originated from a genuine invocation of the gateway program (e.g., via full transaction replay/simulation, program-ID/log-bracket checking, or PDA-derivation checks on the emitting account), this is a critical path to unauthorized finalize/revert/refund of value on Push Chain, matching the "forged... outbound, ballot... state accepted through user-reachable flows with honest validators and honest nodes" in-scope impact.

### Likelihood Explanation
High from a mechanics standpoint — deploying a Solana program and crafting a matching `sol_log_data` payload is a routine, permissionless, unprivileged action requiring no more than standard devnet/mainnet transaction fees. The gap is a structural authentication omission (no invocation-context binding) rather than a probabilistic or race-dependent bug.

### Recommendation
Bind event parsing to the actual gateway program's CPI invocation frame: track `"Program <gatewayAddress> invoke [n]"` / `"Program <gatewayAddress> success"` brackets in `tx.Meta.LogMessages` and only feed log lines that fall strictly within that frame (and ideally only the top invocation depth expected for the given instruction) into `determineEventType`/`ParseEvent`. Do not rely solely on `GetSignaturesForAddress` inclusion or on discriminator-text matching across the full flat log array.

### Proof of Concept
Not independently executed within this review (index-only access); the "fast validation" method proposed in the question — emit crafted gateway logs from an attacker-deployed program on a local validator, including the gateway address as an unused account to satisfy `GetSignaturesForAddress`, and compare the resulting `store.Event` — is the appropriate way to confirm end-to-end impact, and should be run by a background agent with repo/terminal access to also confirm whether the downstream `x/` voting module adds any independent re-validation that would mitigate this. [5](#0-4)

### Citations

**File:** universalClient/chains/svm/event_listener.go (L273-332)
```go
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
	}

	return processed, nil
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

**File:** universalClient/chains/svm/event_parser.go (L237-286)
```go
func decodeUniversalTxEvent(data []byte, logger zerolog.Logger) (*common.UniversalTx, error) {
	if len(data) < 120 {
		logger.Warn().
			Int("data_len", len(data)).
			Msg("data might be too short for complete TxWithFunds event")
	}

	offset := 8
	payload := &common.UniversalTx{}

	// Parse sender (32 bytes)
	if len(data) < offset+32 {
		return nil, fmt.Errorf("not enough data for sender")
	}
	sender := solana.PublicKey(data[offset : offset+32])
	// Convert sender to hex format
	senderHex, err := base58ToHex(sender.String())
	if err != nil {
		logger.Warn().Err(err).Msg("failed to convert sender to hex, using base58")
		payload.Sender = sender.String()
	} else {
		payload.Sender = senderHex
	}
	offset += 32

	// Parse recipient (20 bytes - byte20 format)
	if len(data) < offset+20 {
		return nil, fmt.Errorf("not enough data for recipient")
	}
	// Convert 20 bytes to hex string (0x + 40 hex chars)
	recipientBytes := data[offset : offset+20]
	payload.Recipient = "0x" + hex.EncodeToString(recipientBytes)
	offset += 20

	// Parse bridge_token (32 bytes)
	if len(data) < offset+32 {
		return nil, fmt.Errorf("not enough data for bridge_token")
	}
	bridgeToken := solana.PublicKey(data[offset : offset+32])
	payload.Token = bridgeToken.String()
	offset += 32

	// Parse bridge_amount (8 bytes)
	if len(data) < offset+8 {
		return nil, fmt.Errorf("not enough data for bridge_amount")
	}
	bridgeAmount := binary.LittleEndian.Uint64(data[offset : offset+8])
	payload.Amount = fmt.Sprintf("%d", bridgeAmount)
	offset += 8

```
