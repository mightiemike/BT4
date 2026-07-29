# Finding

The core assumption in Q0248 — that `parseSendFundsEvent` might internally swap the meaning of sender/recipient/token/refund fields through "address normalization" — does not hold up: the Borsh field walk in `decodeUniversalTxEvent` uses fixed offsets and explicit bounds checks before each field [1](#0-0) , and truncated/short data simply returns early with a partial `UniversalTx` rather than reassigning meaning across fields [2](#0-1) . `base58ToHex`/hex conversions are one-way formatting only, they never re-route recipient/sender/token identity [3](#0-2) .

However, tracing the full ingestion path surfaced a real, more severe defect in the same surface: **event-type classification has no binding to which program actually emitted the log.**

- `EventListener.processSignatureBatch` fetches every log line for any transaction that merely references the gateway address, and scans **all** `tx.Meta.LogMessages` for a `"Program data: "` prefix, without checking which program in the transaction's invoke stack produced that line [4](#0-3) .
- `determineEventType` classifies purely by decoding the base64 payload and matching the first 8 bytes against a registry-configured (but publicly known/derivable) discriminator table [5](#0-4) .
- `ParseEvent`/`parseSendFundsEvent` then trust that classification unconditionally and construct a full inbound `store.Event` from whatever bytes follow the discriminator [6](#0-5) .

Because Solana's `sol_log_data`/`emit!` output is just flat text in `LogMessages` with no cryptographic or structural tie back to the emitting program recorded by this code, an unprivileged attacker can deploy their own program, include the real gateway address as an incidental account reference (satisfying `GetSignaturesForAddress`), and have their own program log an arbitrary `"Program data: "` line whose first 8 bytes match the known `send_funds` (or `finalize_universal_tx`/`revert_universal_tx`/`funds_rescued`) discriminator, followed by fully attacker-chosen sender, recipient (EVM address), token, amount, payload, revert-recipient and tx_type bytes. Every honest Universal Validator, running identical unmodified code against the identical on-chain transaction, will independently parse the same forged event the same way and vote it in, letting an honest 2/3+ quorum finalize a completely fabricated inbound (or outbound-observation) event — leading to unauthorized PRC20 mint / payload execution on Push Chain with no real backing deposit, all without needing any malicious validator.

### Title
Forged inbound/outbound gateway events via unauthenticated `Program data:` log injection - (File: universalClient/chains/svm/event_listener.go, universalClient/chains/svm/event_parser.go)

### Summary
`EventListener.determineEventType`/`ParseEvent`/`parseSendFundsEvent` classify and parse Solana gateway events by scanning a transaction's flat `LogMessages` for any line matching a known 8-byte discriminator, without verifying that the log was actually emitted by the configured gateway program. An attacker can get an arbitrary attacker-controlled program's log line included in a transaction that merely references the gateway address, forging a fully attacker-controlled `send_funds` (or outbound observation) event.

### Finding Description
`processSignatureBatch` pulls signatures via `GetSignaturesForAddress(gatewayAddr, ...)` — an RPC filter that only requires the gateway pubkey to appear among the transaction's referenced accounts, not that the gateway program was invoked as the log's source [7](#0-6) . It then iterates every log line in the fetched transaction and classifies solely on discriminator bytes [4](#0-3) [5](#0-4) . Discriminators are not secrets (Anchor-style discriminators are deterministic hashes of the event name), so an attacker can reuse the exact bytes the real gateway uses. `parseSendFundsEvent`/`decodeUniversalTxEvent` then accept the remaining bytes as fully trusted sender/recipient/token/amount/payload/revert-recipient/tx_type fields with no cross-check against the actual gateway account state or CPI provenance [8](#0-7) .

### Impact Explanation
This breaks the "honest-validator finalization path" invariant: user-created source events must not let honest UVs converge on a forged ballot. Since parsing is deterministic and identical across all validators, an attacker can get a fabricated `send_funds` inbound (or `finalize_universal_tx`/`revert_universal_tx`/`funds_rescued` outbound observation) accepted by 2/3+ honest votes, leading to unauthorized PRC20 mint, unauthorized payload execution, or unauthorized refund/release accounting on Push Chain with no genuine underlying deposit — a direct fund-loss/unauthorized-mint scenario in Push Chain's allowed-impact scope.

### Likelihood Explanation
High. No privileged access, TSS keys, or validator collusion is required — only the ability to submit an ordinary Solana transaction that (a) references the gateway address as an account and (b) invokes an attacker-deployed program that calls `sol_log_data`/`emit!` with attacker-chosen bytes. Discriminators are public/derivable.

### Recommendation
Bind log classification/parsing to the actual invoking program: correlate each `"Program data: "` line to the enclosing `"Program <gatewayAddress> invoke [...]"` / `"Program <gatewayAddress> success"` bracket in `LogMessages` (or use `innerInstructions`/`meta` program-index data) before accepting it as a gateway event, and reject any `Program data` line that is not nested under the configured gateway program's own invocation.

### Proof of Concept
1. Deploy a throwaway Solana program that on invocation calls `sol_log_data(discriminator_bytes || crafted_borsh_payload)` where `discriminator_bytes` equals the on-chain gateway's `send_funds` discriminator (obtainable from any public gateway transaction) and `crafted_borsh_payload` encodes an attacker-chosen sender, a Push Chain recipient address the attacker controls, a large `bridge_amount`, and an arbitrary token.
2. Submit one transaction containing an instruction that references the real gateway program's address as an account (no funds transfer required) plus an instruction invoking the throwaway program.
3. Run `universalClient`'s SVM event listener (or a local Solana validator + the listener) against this transaction; observe that `ParseEvent`/`parseSendFundsEvent` produces a `store.Event` identical in shape to a genuine deposit, with attacker-chosen `Sender`/`Recipient`/`Amount`/`Token`.
4. Show that this event is submitted via `VoteInbound` and, replicated across independently-run honest validator instances, reaches quorum and finalizes a `UniversalTx` mint with no corresponding real gateway deposit. [9](#0-8) [10](#0-9)

### Citations

**File:** universalClient/chains/svm/event_parser.go (L28-42)
```go
// base58ToHex converts a base58 encoded string to hex format (0x...)
func base58ToHex(base58Str string) (string, error) {
	if base58Str == "" {
		return "0x", nil
	}

	// Decode base58 to bytes
	decoded, err := base58.Decode(base58Str)
	if err != nil {
		return "", fmt.Errorf("failed to decode base58: %w", err)
	}

	// Convert to hex with 0x prefix
	return "0x" + hex.EncodeToString(decoded), nil
}
```

**File:** universalClient/chains/svm/event_parser.go (L46-99)
```go
func ParseEvent(log string, signature string, slot uint64, logIndex uint, eventType string, chainID string, logger zerolog.Logger) *store.Event {
	switch eventType {
	case EventTypeSendFunds:
		return parseSendFundsEvent(log, signature, slot, logIndex, chainID, logger)
	case EventTypeFinalizeUniversalTx, EventTypeRevertUniversalTx, EventTypeFundsRescued:
		return parseOutboundObservationEvent(log, signature, slot, logIndex, chainID, logger)
	default:
		logger.Debug().
			Str("event_type", eventType).
			Str("signature", signature).
			Msg("unknown event type, skipping")
		return nil
	}
}

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

**File:** universalClient/chains/svm/event_parser.go (L244-317)
```go
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

```

**File:** universalClient/chains/svm/event_listener.go (L211-234)
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

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L18-52)
```go
func (k Keeper) VoteInbound(ctx context.Context, universalValidator sdk.ValAddress, inbound types.Inbound) error {
	// Canonicalize first so every derived key + the stored inbound use one
	// representation per logical event.
	inbound.Canonicalize()

	k.Logger().Info("vote inbound received",
		"validator", universalValidator.String(),
		"source_chain", inbound.SourceChain,
		"tx_hash", inbound.TxHash,
		"tx_type", inbound.TxType.String(),
		"sender", inbound.Sender,
	)

	// Check inbound enabled before any state changes
	enabled, err := k.uregistryKeeper.IsChainInboundEnabled(ctx, inbound.SourceChain)
	if err != nil {
		return errors.Wrap(err, "failed to check inbound enabled")
	}
	if !enabled {
		k.Logger().Warn("vote inbound rejected: chain inbound disabled", "source_chain", inbound.SourceChain)
		return fmt.Errorf("inbound is disabled for chain %s", inbound.SourceChain)
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)

	// Step 1: Derive UTX key from the original inbound data (source_chain:tx_hash:log_index)
	universalTxKey := types.GetInboundUniversalTxKey(inbound)
	found, err := k.HasUniversalTx(ctx, universalTxKey)
	if err != nil {
		return errors.Wrap(err, "failed to check UniversalTx")
	}
	if found {
		k.Logger().Warn("vote inbound rejected: utx already exists", "utx_key", universalTxKey)
		return fmt.Errorf("universal tx with key %s already exists", universalTxKey)
	}
```
