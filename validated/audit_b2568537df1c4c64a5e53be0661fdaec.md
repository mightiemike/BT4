## Assessment: Valid vulnerability, but not exactly as described in the question

The question hypothesizes a length/discriminator/field-parsing confusion inside `decodeUniversalTxEvent`, but that function's own byte-layout parsing is careful (bounds-checked at every step: sender, recipient, token, amount, variable-length `data`, revert-recipient, tx_type, variable-length signature, from_cea) — see [1](#0-0) . There is no evidence that well-formed bytes get "normalized" into a different address than what was encoded; each field is read at a fixed, sequentially-advancing offset.

The real defect is upstream of `parseSendFundsEvent`, in how logs are selected for parsing in `event_listener.go`:

- `processSlotRange` fetches every transaction whose account list references the configured `gatewayAddress` via `GetSignaturesForAddress` [2](#0-1) .
- `processSignatureBatch` then iterates **every** log line in `tx.Meta.LogMessages` for that transaction and calls `determineEventType`/`ParseEvent` on each line independently [3](#0-2) .
- `determineEventType` and `parseSendFundsEvent` only check that a line starts with `"Program data: "` and that the first 8 decoded bytes match a known discriminator [4](#0-3) [5](#0-4) .

Neither function tracks the Solana log invoke-stack (`Program <id> invoke [depth]` / `success`) to confirm that the `"Program data:"` line was actually emitted by the gateway program itself, as opposed to by any other program invoked within the same transaction (e.g., via a CPI from an attacker-deployed program, or a `sol_log_data` call issued directly by a user program). Solana's `getSignaturesForAddress`/log inclusion only requires that the gateway address appear somewhere in the transaction's account list — it does not require the gateway program to actually execute or emit that log line.

This means an attacker could:
1. Deploy or use their own program.
2. Build a single transaction that references the gateway address as an account (satisfying the RPC filter) and, from their own program, emit a raw log line `Program data: <base64>` whose first 8 bytes match the known/public `send_funds` discriminator, followed by attacker-chosen sender, recipient (mint target on Push Chain), bridge token, amount, payload, and revert-recipient bytes.
3. Have this forged log accepted by `parseSendFundsEvent` as a legitimate inbound `send_funds` event, since nothing binds the log line to the gateway program's actual execution.

If downstream Push Chain finalization does not independently re-verify that value was actually locked/transferred on-chain to the gateway/vault for that specific signature (i.e., it trusts the parsed log content), this becomes a path to a forged inbound event that could result in unauthorized minting/release on Push Chain for funds that were never deposited. I could not fully trace the downstream inbound-voting/finalization code in this pass to confirm whether a secondary on-chain balance/transfer check exists there, which would be needed to fully close or confirm this gap.

### Title
Unbound log-to-program attribution in SVM gateway event ingestion allows forged inbound `send_funds` events - (File: universalClient/chains/svm/event_listener.go, universalClient/chains/svm/event_parser.go)

### Summary
The SVM event listener treats any `"Program data:"` log line inside a transaction that merely references the gateway address as an authentic gateway event, without verifying the line was emitted by the gateway program's own execution frame.

### Finding Description
`processSignatureBatch` iterates linearly over `tx.Meta.LogMessages` and calls `ParseEvent`/`parseSendFundsEvent` on any line beginning with `"Program data: "` whose first 8 bytes match a public discriminator [3](#0-2) . There is no cross-check against the Solana log invoke stack to ensure the data log originates from the gateway program ID rather than any other program invoked in the same transaction. Since `getSignaturesForAddress` only requires the gateway address to be present among the transaction's referenced accounts (not necessarily invoked), an attacker-controlled program executed in the same transaction can emit a spoofed `sol_log_data` line matching the expected discriminator and arbitrary payload bytes, which `decodeUniversalTxEvent` will faithfully decode into sender, recipient, token, amount, payload, and revert-recipient fields [1](#0-0) .

### Impact Explanation
If the honest-validator finalization pipeline relies on this parsed event content without independently re-verifying an actual on-chain fund transfer/lock at the gateway/vault for the given signature, this allows an unprivileged attacker to inject a fabricated inbound event, potentially causing unauthorized minting or release of value on Push Chain for funds that were never deposited.

### Likelihood Explanation
Requires only submitting an ordinary, unprivileged Solana transaction that references the known public gateway address and includes a log emission from any program (including one deployed by the attacker) with a spoofed discriminator — no privileged access needed.

### Recommendation
Attribute each `"Program data:"` log to its emitting program by tracking the `Program <id> invoke`/`success` nesting in `tx.Meta.LogMessages`, and only accept data logs emitted from within the gateway program's own invocation frame. Additionally, downstream finalization should independently verify the underlying fund movement (e.g., vault/token account delta) rather than trusting parsed log content alone.

### Proof of Concept
1. Deploy a minimal Solana program that calls `sol_log_data` with bytes: `[known 8-byte send_funds discriminator] + [attacker sender pubkey] + [attacker-chosen recipient 20 bytes] + [attacker-chosen token pubkey] + [large amount] + [empty payload] + [attacker revert-recipient pubkey] + [tx_type] + [empty sig]`.
2. Submit a single transaction that includes an instruction referencing the real gateway program's address as one of its accounts (to satisfy `getSignaturesForAddress` filtering) alongside an instruction invoking the attacker's program from step 1.
3. Observe that `EventListener.processSignatureBatch` fetches this transaction (because the gateway address is referenced) and `ParseEvent`/`parseSendFundsEvent` parses the attacker's spoofed log line into a `store.Event` with `Type: EventTypeInbound`, indistinguishable from a genuine gateway-emitted event.

### Citations

**File:** universalClient/chains/svm/event_parser.go (L61-99)
```go
// parseSendFundsEvent parses a sendFunds event as UniversalTx
func parseSendFundsEvent(log string, signature string, slot uint64, logIndex uint, chainID string, logger zerolog.Logger) *store.Event {
	if !strings.HasPrefix(log, "Program data: ") {
		return nil
	}

	eventData := strings.TrimPrefix(log, "Program data: ")
	decoded, err := base64.StdEncoding.DecodeString(eventData)
	if err != nil {
		return nil
	}

	if len(decoded) < 8 {
		return nil
	}

	// Create EventID in format: signature:LogIndex
	eventID := fmt.Sprintf("%s:%d", signature, logIndex)

	logger.Debug().
		Str("event_id", eventID).
		Str("signature", signature).
		Uint("log_index", logIndex).
		Uint64("slot", slot).
		Msg("processing sendFunds event")

	// Create store.Event
	event := &store.Event{
		EventID:           eventID,
		BlockHeight:       slot,
		Type:              store.EventTypeInbound, // Gateway events from external chains are INBOUND
		Status:            store.StatusPending,
		ExpiryBlockHeight: 0, // Will be set based on confirmation type if needed
	}

	// Parse event data from this log
	parseUniversalTxEvent(event, decoded, logIndex, chainID, logger)

	return event
```

**File:** universalClient/chains/svm/event_parser.go (L237-371)
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

**File:** universalClient/chains/svm/event_listener.go (L211-228)
```go
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
```

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

**File:** universalClient/chains/svm/event_listener.go (L398-422)
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
```
