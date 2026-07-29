## Analysis

`processSignatureBatch` fetches candidate signatures via `GetSignaturesForAddress(ctx, gatewayAddr, ...)` [1](#0-0) , which Solana's RPC returns for *any* transaction that merely references the gateway pubkey as one of its account keys — it does not require that the gateway program actually be invoked. The listener then calls `GetTransaction` [2](#0-1)  and unconditionally iterates every entry of `tx.Meta.LogMessages`, calling `determineEventType(log)` on each line with zero attribution of which program emitted it [3](#0-2) .

`determineEventType`/`parseSendFundsEvent` only checks that a line has the `"Program data: "` prefix and that the first 8 decoded bytes equal a configured discriminator [4](#0-3) , confirmed by the unit test that builds a synthetic `"Program data: " + base64(discriminator+payload)` string with no association to any specific program and gets it accepted [5](#0-4) . Solana log output does not embed the emitting program's identity inline in a `"Program data:"` line — only the surrounding `"Program <id> invoke [...]"` / `"Program <id> success"` frame lines carry that information, and this code never parses or checks that frame.

## Title
Forged "Program data:" log from a non-gateway program is accepted as a genuine sendFunds inbound event - (File: universalClient/chains/svm/event_listener.go, universalClient/chains/svm/event_parser.go, universalClient/chains/svm/rpc_client.go)

### Summary
The SVM event listener treats any `"Program data:"` log line inside a transaction that merely references the configured gateway pubkey as an account key as if it were emitted by the gateway program itself, with no verification of the emitting program.

### Finding Description
`processSlotRange`/`processSignatureBatch` pulls candidate signatures using `GetSignaturesForAddress(gatewayAddr, ...)`. This RPC method returns transactions where the address appears anywhere among the transaction's account keys — not only when that address's program is actually invoked. An unprivileged attacker can build a transaction that:
1. Lists the gateway program's pubkey as an inert, read-only account (no invocation), satisfying the `GetSignaturesForAddress` filter.
2. Invokes an attacker-owned decoy program that simply logs an arbitrary string (`msg!`/`sol_log`) formatted as `"Program data: " + base64(payload)`, where `payload` begins with the 8-byte discriminator configured for `sendFunds` and is followed by attacker-chosen sender/recipient/bridge_token/bridge_amount fields per the Borsh layout consumed by `decodeUniversalTxEvent` [6](#0-5) .

Because `GetTransaction` returns `Meta.LogMessages` unfiltered and `processSignatureBatch` scans every log line regardless of which program emitted it, and `determineEventType`/`parseSendFundsEvent` verify only the discriminator bytes with no check against the surrounding `"Program <id> invoke"`/`"Program <id> success"` bracketing that would identify the true emitting program, the forged log is accepted and parsed into a `store.Event` of type `EventTypeInbound` with attacker-chosen recipient/amount/token [7](#0-6) .

Because this is a deterministic code-level flaw in the shared universal client, every honest universal validator running unmodified node code observes the same forged, immutable on-chain log identically, and will converge on the same (wrong) candidate event during voting/finalization — this is not a malicious-validator scenario, it is honest nodes being fed corrupted input from a flawed parser.

### Impact Explanation
A successful forged event, if it survives downstream confirmation/voting without an independent gateway-authenticity check, results in an inbound `sendFunds` UniversalTx being accepted with attacker-chosen recipient, token, and amount despite no real deposit into the gateway/vault ever occurring — i.e., unauthorized minting of PRC20/native value to an arbitrary recipient, a direct fund/accounting-corruption impact in scope.

### Likelihood Explanation
The attacker only needs to submit an ordinary, unprivileged Solana transaction referencing the gateway pubkey as an account and invoking their own program to emit a crafted log — this requires no special privilege, gateway state, or validator collusion, making it straightforward to attempt. Whether it results in an actual mint further depends on whether subsequent voting/finalization stages in `x/` perform additional authenticity checks (e.g., re-deriving the event strictly from instructions that call the gateway program) before finalizing; the SVM listener code shown here itself performs no such check.

### Recommendation
When parsing SVM logs, correlate each `"Program data:"` line to the enclosing `"Program <id> invoke [...]" / "Program <id> success"` frame in `tx.Meta.LogMessages`, and only treat a log as a genuine gateway event if the frame's program ID equals the configured gateway address. Reject/skip any `"Program data:"` line whose enclosing invocation is not attributable to the gateway program.

### Proof of Concept
1. Deploy a throwaway devnet program that, when invoked, emits `msg!("Program data: {}", base64(payload))` where `payload` is the sendFunds discriminator followed by attacker-controlled sender/recipient/token/amount bytes.
2. Submit a transaction that lists the real gateway program's pubkey as a non-signer account (no CPI to it) and invokes the throwaway program.
3. Run `GetSignaturesForAddress(gatewayAddr, ...)` and confirm the crafted transaction's signature is returned.
4. Run the listener pipeline (`processSignatureBatch` → `determineEventType` → `ParseEvent`) against this transaction and assert that a `store.Event` of type `EventTypeInbound` is created with the attacker-chosen recipient/amount, despite no invocation of the real gateway program and no real deposit.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L228-231)
```go
		batch, err := el.rpcClient.GetSignaturesForAddress(ctx, gatewayAddr, beforeSig)
		if err != nil {
			return fmt.Errorf("failed to get signatures (page %d): %w", page, err)
		}
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

**File:** universalClient/chains/svm/rpc_client.go (L313-330)
```go
// GetTransaction gets a transaction by signature
func (rc *RPCClient) GetTransaction(ctx context.Context, signature solana.Signature) (*rpc.GetTransactionResult, error) {
	var tx *rpc.GetTransactionResult
	err := rc.executeWithFailover(ctx, "get_transaction", func(client *rpc.Client) error {
		var innerErr error
		maxVersion := uint64(0)
		tx, innerErr = client.GetTransaction(
			ctx,
			signature,
			&rpc.GetTransactionOpts{
				Encoding:                       solana.EncodingBase64,
				MaxSupportedTransactionVersion: &maxVersion,
			},
		)
		return innerErr
	})
	return tx, err
}
```

**File:** universalClient/chains/svm/event_parser.go (L61-97)
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
```

**File:** universalClient/chains/svm/event_parser.go (L236-284)
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
```

**File:** universalClient/chains/svm/event_listener_test.go (L221-228)
```go
	t.Run("matching discriminator returns event type", func(t *testing.T) {
		payload := append(discriminatorBytes, []byte("extra data here")...)
		encoded := base64.StdEncoding.EncodeToString(payload)
		log := "Program data: " + encoded

		eventType := el.determineEventType(log)
		assert.Equal(t, EventTypeSendFunds, eventType)
	})
```
