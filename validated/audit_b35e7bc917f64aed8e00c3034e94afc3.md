Based on the code I traced, this holds up as a real (though bounded) vulnerability in the log-observation logic itself, but with important caveats about how much damage it can actually cause given the on-chain guard rails.

## Confirmed mechanism

`parseOutboundObservationEvent` in `universalClient/chains/svm/event_parser.go` never checks which program actually emitted a given `Program data:` log line — it only checks the base64 payload's leading 8-byte discriminator. [1](#0-0) 

The listener that feeds it selects candidate transactions via `GetSignaturesForAddress(gatewayAddr)`, which returns *any* transaction that references the gateway address as an account key (not only ones that CPI into the gateway program), and then scans **every** log line in that transaction for a matching discriminator with no association to which program emitted it: [2](#0-1) [3](#0-2) 

So an attacker's own program, invoked in a self-controlled transaction that merely lists the gateway address as a (read-only, non-signer) account, can emit a `sol_log_data` line whose first 8 bytes equal `sha256("global:revert_universal_tx")[:8]` (a public, deterministic value) plus fabricated `sub_tx_id`/`universal_tx_id`/`gas_used` bytes. This is indeed stored as a `store.Event{Type: store.EventTypeOutbound}` with no on-chain corroboration that the gateway program actually ran a revert.

## Where the claim breaks down

1. **The parsed payload only carries `TxID`, `UniversalTxID`, `GasFeeUsed`** — not a revert recipient or refund amount. Those fields (`revert_recipient`, `amount`) are only present in the *outbound instruction builder* (`buildRevertData` in `tx_builder.go`), not in the *observation* parser. So the specific claim of "corrupting the revert recipient/refund amount" is not accurate for this code path — those values aren't extracted or forwarded here. [4](#0-3) 

2. **The forged local event does not become canonical state by itself.** `EventProcessor.processOutboundEvent` builds an `OutboundObservation{Success:true, TxHash, GasFeeUsed}` and submits `MsgVoteOutbound` on-chain. [5](#0-4) 
`VoteOutbound` on-chain requires `utxId` to resolve to an existing `UniversalTx` and `outboundId` to match an outbound entry already in `Status_PENDING` inside that UTX; unknown IDs are rejected outright, and finalization additionally requires a ≥2/3 quorum of honest Universal Validators converging on the *same* `ObservedTx` ballot key (`sha256(utxId:outboundId:marshal(observedTx))`). [6](#0-5) [7](#0-6) 

So for corruption of a *real* pending UniversalTx to occur, the attacker would need honest majority UVs to independently observe the identical forged Solana transaction on the real chain (since each runs its own RPC/listener) and reuse a real, still-pending `utxId`/`outboundId` pair (which is public/queryable). If they do that, all honest UVs would submit the same false `Success=true, GasFeeUsed=<attacker value>` observation and reach quorum — this would genuinely mark a real pending outbound `OBSERVED` (success) with an attacker-chosen `gas_fee_used` even though nothing happened on the real gateway, corrupting gas-fee/refund accounting downstream in `FinalizeOutbound`. That is an in-scope accounting-corruption impact reachable by an unprivileged attacker crafting an ordinary self-controlled Solana transaction, not requiring malicious validators — consistent with "honest-validator finalization path" being tricked by a forged inbound-observable artifact.

However, `revert_universal_tx` and `finalize_universal_tx` are parsed identically by `parseOutboundObservationEvent` (both routed to it, per `ParseEvent`), and `buildOutboundObservation` always sets `Success: true` regardless of which discriminator matched. [8](#0-7) [9](#0-8) 
This means the specific "REVERT" semantics and "corrupting the revert recipient/refund amount" framing in the question don't match what this code path actually produces — the vote is always a success observation with only `GasFeeUsed` as attacker-influenced data, not a revert-with-recipient/amount corruption.

## Conclusion

There is a genuine gap — the SVM event listener/parser does not bind observed `Program data:` logs to the program ID that emitted them, allowing an attacker to inject a spoofed gateway-formatted log via their own program merely by naming the gateway address as an account. This can, if it reaches quorum among honest UVs (all observing the same real, attacker-crafted transaction), cause a real pending outbound to be marked `OBSERVED`/success with an attacker-controlled `GasFeeUsed`, corrupting gas-refund accounting for that UniversalTx. But the exploit's premise as stated (forging a "REVERT" event that corrupts "revert recipient/refund amount") does not match the actual code: `parseOutboundObservationEvent` doesn't parse or propagate revert recipient/amount at all, and both finalize/revert discriminators are treated identically and always voted as `Success: true`. The real, narrower bug is a missing program-ID/log-provenance check in `determineEventType`/`processSignatureBatch`, enabling forged `GasFeeUsed` injection into a real pending outbound's success observation, not a revert-destination hijack.

### Title
Missing program-ID binding on SVM gateway log parsing allows forged gas-fee observation on real pending outbounds - (File: `universalClient/chains/svm/event_listener.go`, `universalClient/chains/svm/event_parser.go`)

### Summary
The SVM event listener treats any `Program data:` log line inside any transaction that merely references the gateway address as an account as a legitimate gateway event, without verifying the log was emitted via a CPI from the actual gateway program.

### Finding Description
`processSignatureBatch` iterates all `tx.Meta.LogMessages` for every signature returned by `GetSignaturesForAddress(gatewayAddr)` (which matches on account-key membership, not program invocation), and `determineEventType`/`parseOutboundObservationEvent` accept any log with the correct 8-byte discriminator prefix regardless of the emitting program. An attacker can deploy a trivial program, invoke it in a self-signed transaction that lists the real gateway address as a read-only account, and emit a fabricated `Program data:` log matching `finalize_universal_tx`'s or `revert_universal_tx`'s discriminator with a real, publicly-known pending `utxId`/`outboundId` and an attacker-chosen `gas_used`.

### Impact Explanation
If honest UVs independently observe this same crafted transaction on the real Solana chain, they converge on the same forged `OutboundObservation{Success:true, GasFeeUsed:<attacker value>}` ballot and can finalize a real pending outbound with a false success/gas-fee record, corrupting the gas-refund accounting handled in `FinalizeOutbound`, before the outbound was ever actually processed by the real gateway. This is bounded — it does not corrupt revert recipient or bridge amount, since those fields are never parsed by this observation path.

### Likelihood Explanation
Requires only an ordinary, unprivileged Solana transaction; no gateway CPI, signature, or privileged role needed. Requires knowledge of a real pending `utxId`/`outboundId` pair, which is publicly queryable via `PendingOutbounds`.

### Recommendation
Bind log parsing to program provenance: only accept `Program data:` logs that occur within the gateway program's own invoke/success bracket (or use `GetTransaction`'s inner-instruction/log-index correlation to the gateway program ID) instead of scanning raw `LogMessages` unconditionally.

### Proof of Concept
A Go test constructing a `solanarpc.GetTransactionResult` whose `Meta.LogMessages` contains a `Program data:` line with `finalize_universal_tx`'s discriminator plus fabricated `sub_tx_id`/`universal_tx_id`/`gas_used`, run through `processSignatureBatch`, shows the event is stored as `store.EventTypeOutbound` with no validation that the discriminator was emitted by the actual gateway program ID.

### Citations

**File:** universalClient/chains/svm/event_parser.go (L46-58)
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
```

**File:** universalClient/chains/svm/event_parser.go (L116-135)
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

```

**File:** universalClient/chains/svm/event_parser.go (L164-169)
```go
	// Create OutboundEvent payload
	payload := common.OutboundEvent{
		TxID:          txID,
		UniversalTxID: universalTxID,
		GasFeeUsed:    fmt.Sprintf("%d", gasUsed),
	}
```

**File:** universalClient/chains/svm/event_listener.go (L298-326)
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
```

**File:** universalClient/chains/svm/rpc_client.go (L295-311)
```go
// GetSignaturesForAddress gets transaction signatures for an address. If
// `before` is the zero signature, fetching starts from the most recent block;
// otherwise it returns signatures strictly older than `before`, enabling
// backward pagination.
func (rc *RPCClient) GetSignaturesForAddress(ctx context.Context, address solana.PublicKey, before solana.Signature) ([]*rpc.TransactionSignature, error) {
	var opts *rpc.GetSignaturesForAddressOpts
	if !before.IsZero() {
		opts = &rpc.GetSignaturesForAddressOpts{Before: before}
	}
	var signatures []*rpc.TransactionSignature
	err := rc.executeWithFailover(ctx, "get_signatures_for_address", func(client *rpc.Client) error {
		var innerErr error
		signatures, innerErr = client.GetSignaturesForAddressWithOpts(ctx, address, opts)
		return innerErr
	})
	return signatures, err
}
```

**File:** universalClient/chains/common/event_processor.go (L164-177)
```go
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

**File:** universalClient/chains/common/event_processor.go (L383-394)
```go
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
```

**File:** x/uexecutor/keeper/msg_vote_outbound.go (L30-69)
```go
	// Step 1: Fetch UniversalTx
	utx, found, err := k.GetUniversalTx(ctx, utxId)
	if err != nil {
		return err
	}
	if !found {
		return fmt.Errorf("UniversalTx not found: %s", utxId)
	}
	if utx.OutboundTx == nil {
		return fmt.Errorf("no outbound tx found in UniversalTx %s", utxId)
	}

	// Step 2: Find outbound by id
	var outbound types.OutboundTx
	found = false
	for _, ob := range utx.OutboundTx {
		if ob.Id == outboundId {
			outbound = *ob
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("outbound %s not found in UniversalTx %s", outboundId, utxId)
	}

	// Canonicalize the observed tx hash for the destination chain so encoding
	// variants of the same observation land on one ballot.
	observedTx.TxHash = utils.LenientCanonicalizeTxHash(outbound.DestinationChain, observedTx.TxHash)
	observedTx.GasFeeUsed = strings.TrimSpace(observedTx.GasFeeUsed)
	observedTx.ErrorMsg = strings.TrimSpace(observedTx.ErrorMsg)

	// Prevent double-finalization
	if outbound.OutboundStatus != types.Status_PENDING {
		k.Logger().Warn("vote outbound rejected: outbound already finalized",
			"outbound_id", outboundId,
			"status", outbound.OutboundStatus.String(),
		)
		return fmt.Errorf("outbound with key %s is already finalized", outboundId)
	}
```

**File:** x/uexecutor/keeper/voting.go (L82-97)
```go
	ballotKey, err := types.GetOutboundBallotKey(utxId, outboundId, observedTx)
	if err != nil {
		return false, false, err
	}

	universalValidatorSet, err := k.uvalidatorKeeper.GetEligibleVoters(ctx)
	if err != nil {
		return false, false, err
	}

	// number of validators
	totalValidators := len(universalValidatorSet)

	// votesNeeded = ceil(2/3 * totalValidators)
	// >2/3 quorum similar to tendermint
	votesNeeded := (types.VotesThresholdNumerator*totalValidators)/types.VotesThresholdDenominator + 1
```
