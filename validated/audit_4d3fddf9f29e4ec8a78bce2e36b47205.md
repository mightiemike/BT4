## Title
SVM gateway event listener trusts unbound `Program data:` logs, allowing forged inbound `UniversalTx` events from a non-gateway program - (File: `universalClient/chains/svm/event_listener.go`)

### Summary
`processSlotRange` fetches signatures with `rpcClient.GetSignaturesForAddress(gatewayAddr, ...)` [1](#0-0)  and then `processSignatureBatch` iterates over **every** log line of the fetched transaction, matching purely on a `Program data: ` prefix + 8-byte discriminator, with no check on which program frame emitted the log [2](#0-1) . `determineEventType` similarly only decodes the base64 payload and looks up the discriminator in a map, independent of which program is currently executing [3](#0-2) .

### Finding Description
Solana's `GetSignaturesForAddress` RPC returns any transaction that references the given address in its account list — as a writable/readonly account, not necessarily as an invoked program. An unprivileged attacker can therefore:

1. Deploy their own Solana program.
2. Build a transaction that invokes their own program, and simply lists the real gateway program's pubkey as a passive (e.g., readonly, non-signer) account reference.
3. Inside their own program, call `sol_log_data` (i.e., emit a `Program data: <base64>` log) whose first 8 bytes match one of the gateway's known event discriminators (e.g., `send_funds`), followed by fully attacker-chosen bytes for `sender`, `recipient` (destination EVM/UEA address), `token`, `amount`, `payload`, `revert_recipient`, `tx_type`, etc., as parsed in `parseUniversalTxEvent`/`decodeUniversalTxEvent` [4](#0-3) .

Because the listener never checks that the "Program data:" line lies within a `Program <gatewayAddress> invoke [...]` / `success` frame (i.e., that the gateway program itself was the one executing when the log was emitted), this forged log is treated identically to a genuine gateway-emitted event: `ParseEvent` builds a `store.Event` and `chainStore.InsertEventIfNotExists` persists it as a pending inbound event [5](#0-4) .

Since the forged transaction is public on Solana, every honest node running this same universalClient code will independently observe and accept the identical forged event — no malicious validator/UV/relayer assumption is required, satisfying the "honest-validator" constraint from the audit rules.

### Impact Explanation
A forged `send_funds` (inbound UniversalTx) event with attacker-controlled `Recipient`, `Token`, and `Amount` fields being accepted as genuine by all honest observing nodes can lead to unauthorized minting/crediting on Push Chain once this locally-stored event flows through the confirmation/voting pipeline (`event_confirmer.go` and the `x/` inbound voting/finalization path), i.e., forged inbound state accepted through a user-reachable flow — this is an in-scope impact per the Required Impacts (forged inbound state, unauthorized mint).

### Likelihood Explanation
High. No privileged access is required — only the ability to write and deploy an ordinary SPL/Solana program and submit a transaction, which is available to any unprivileged Solana user.

### Recommendation
When scanning `tx.Meta.LogMessages`, track the current program-invocation stack (`Program <pubkey> invoke [n]` / `Program <pubkey> success` markers) and only treat a `Program data:` log as a gateway event if it occurs within the frame where `gatewayAddress` is the currently executing program. Alternatively/additionally, validate `tx.Transaction` instructions to confirm the gateway program ID appears as the invoked program for the relevant instruction, not merely as a referenced account.

### Proof of Concept
1. Mock `GetSignaturesForAddress` to return a signature for a transaction where `gatewayAddress` is included only as a readonly account reference (not the invoked program).
2. Mock `GetTransaction` for that signature to return `tx.Meta.LogMessages` containing an outer `Program <attackerProgram> invoke [1]` block with an inner `Program data: <base64(discriminator || arbitrary attacker fields)>` line, followed by `Program <attackerProgram> success`.
3. Call `processSignatureBatch` (or drive it via `processSlotRange`) and assert `chainStore.InsertEventIfNotExists` is called with a `store.Event` whose `EventData` decodes to the attacker-chosen `Recipient`/`Token`/`Amount`, proving the forged, non-gateway-executed log is stored identically to a genuine gateway event. [6](#0-5)

### Citations

**File:** universalClient/chains/svm/event_listener.go (L216-234)
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
		if err != nil {
			return fmt.Errorf("failed to get signatures (page %d): %w", page, err)
		}
		if len(batch) == 0 {
			break
		}
```

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

**File:** universalClient/chains/svm/event_parser.go (L236-371)
```go
// decodeUniversalTxEvent decodes a TxWithFunds event
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

	// Parse data field length (4 bytes)
	if len(data) < offset+4 {
		logger.Warn().Msg("not enough data for data field length")
		return payload, nil
	}
	dataLen := binary.LittleEndian.Uint32(data[offset : offset+4])
	offset += 4

	// Parse data field
	if len(data) < offset+int(dataLen) {
		logger.Warn().
			Uint32("expected_len", dataLen).
			Int("available", len(data)-offset).
			Msg("not enough data for data field")
		return payload, nil
	}
	if dataLen > 0 {
		dataField := data[offset : offset+int(dataLen)]
		payload.RawPayload = "0x" + hex.EncodeToString(dataField)
		offset += int(dataLen)
	}

	// Parse revert_recipient (Pubkey)
	if len(data) < offset+32 {
		logger.Warn().Msg("not enough data for revert recipient")
		return payload, nil
	}
	revertRecipient := solana.PublicKey(data[offset : offset+32])
	payload.RevertFundRecipient = revertRecipient.String()
	offset += 32

	// Parse tx_type (TxType enum)
	if len(data) <= offset {
		logger.Warn().Msg("not enough data for tx_type, defaulting to Funds")
		payload.TxType = uint(0)
		return payload, nil
	}
	txType := data[offset]
	payload.TxType = uint(txType)
	offset++

	// Parse signature data length (4 bytes)
	if len(data) < offset+4 {
		logger.Warn().Msg("not enough data for signature length")
		return payload, nil
	}
	sigLen := binary.LittleEndian.Uint32(data[offset : offset+4])
	offset += 4

	remainingBytes := len(data) - offset
	if int(sigLen) > remainingBytes {
		logger.Warn().
			Uint32("expected_len", sigLen).
			Int("available", remainingBytes).
			Msg("signature data length exceeds available data, skipping")
		return payload, nil
	}

	if sigLen > 0 {
		sigData := data[offset : offset+int(sigLen)]
		payload.VerificationData = "0x" + hex.EncodeToString(sigData)
		offset += int(sigLen)
	}

	// Parse fromCEA (bool, 1 byte) - if not present, defaults to false
	if len(data) > offset {
		payload.FromCEA = data[offset] != 0
		offset++
	}

	logger.Debug().
		Str("sender", payload.Sender).
		Str("recipient", payload.Recipient).
		Str("bridge_amount", payload.Amount).
		Str("bridge_token", payload.Token).
		Str("raw_payload", payload.RawPayload).
		Str("verification_data", payload.VerificationData).
		Str("revert_recipient", payload.RevertFundRecipient).
		Uint("tx_type", payload.TxType).
		Bool("from_cea", payload.FromCEA).
		Int("total_bytes_parsed", offset).
		Msg("decoded UniversalTx event")

	return payload, nil
}
```
