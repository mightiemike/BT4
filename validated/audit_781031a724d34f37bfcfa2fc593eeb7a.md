## Analysis

The concern is valid: `EventListener.determineEventType` in `universalClient/chains/svm/event_listener.go` only checks the 8-byte discriminator of a base64-decoded `"Program data: "` log line against a public map built from configured `GatewayMethods.EventIdentifier` values, with no verification that the log line was emitted by the actual gateway program. [1](#0-0) 

The listener discovers candidate transactions via `GetSignaturesForAddress(gatewayAddr, ...)`, which returns any transaction that merely references the gateway account (Solana includes accounts that are only listed, not necessarily invoked), and then iterates **every** log line in `tx.Meta.LogMessages` independent of which program produced it. [2](#0-1) 

There is no correlation with the surrounding `Program <id> invoke [...]` / `Program <id> success` bracketing log lines to bind a given `"Program data: "` line to the real gateway program ID. Anchor-style discriminators are deterministic (derived from the event name/method name), and the mapping is built directly from the on-chain/config `GatewayMethods` registry, so these values are not secret — they are exactly reproducible by anyone who knows the event names (`send_funds`, `finalize_universal_tx`, `revert_universal_tx`, `funds_rescued`). [3](#0-2) 

Consequently, an attacker can deploy their own Solana program that emits a self-CPI `sol_log_data` line with a matching discriminator and forged payload bytes, inside a transaction that merely lists the real gateway pubkey as an account (satisfying `GetSignaturesForAddress`). The listener will pick it up, `determineEventType` will misclassify the forged log as a genuine gateway event, and `ParseEvent`/`parseSendFundsEvent`/`parseOutboundObservationEvent` will decode the attacker-controlled bytes into a `store.Event` with attacker-chosen sender, recipient, token, amount, or attacker-chosen `TxID`/`UniversalTxID`/`gas_used` for the outbound-observation events. [4](#0-3) [5](#0-4) 

This forged event is not discarded — it flows through the normal confirmation and vote pipeline. `EventConfirmer.processPendingEvents` only checks slot depth and `tx.Meta.Err`; it never validates that the event's log line came from the gateway program. [6](#0-5) 

Once "confirmed," `EventProcessor.processConfirmedEvents` routes an `EventTypeInbound` event to `processInboundEvent`, which calls `constructInbound` and then `ep.signer.VoteInbound`, broadcasting `MsgVoteInbound` on Push Chain with the attacker-forged sender/recipient/amount/token data as if it were an honest observation by the universal validator running this code. [7](#0-6) [8](#0-7) 

On the chain side, `Keeper.VoteInbound` / `VoteOnInboundBallot` only requires that ≥2/3 of eligible universal validators submit `MsgVoteInbound` with byte-identical `Inbound` payloads for the same ballot key — it performs no additional on-chain validation against the actual SVM gateway account. [9](#0-8) [10](#0-9) 

Critically, since every universal client independently re-derives the same forged event from the same public Solana transaction (the attacker's crafted transaction is itself the canonical, deterministic source of truth being read), **all honest UVs will independently reconstruct the identical bogus `Inbound`/`OutboundObservation` payload** and vote for it — satisfying quorum with entirely honest validators. This is exactly the "forged... inbound/outbound... state accepted through user-reachable flows with honest validators and honest nodes" scenario that is explicitly in scope.

For `send_funds` (inbound), this allows an attacker to fabricate a deposit that never happened, potentially causing the mint/execution pipeline to release synthetic value based on data that no real gateway program emitted — a direct path to unauthorized minting/fund creation without any real deposit. For the outbound-observation events (`finalize_universal_tx`, `revert_universal_tx`, `funds_rescued`), forging these could cause a genuine outbound to be marked finalized/reverted with attacker-controlled `gas_used`/`TxID`/`UniversalTxID`, corrupting canonical UniversalTx state and gas-refund accounting.

I was not able to fully trace the exact downstream execution effects of a forged `send_funds` inbound all the way through minting logic (`inbound.ValidateForExecution`, `handleFailedInboundValidation`, PRC20 mint paths) within the available context, so I cannot confirm with certainty whether some later-stage check (e.g., independent balance-delta validation against the gateway's token account) exists that would neutralize the forged payload before funds move. However, based on what is verifiable, the discriminator-only matching without program-ID binding in `determineEventType`/`ParseEvent` is a real gap in the code as written.

### Title
Gateway event forgery via unauthenticated discriminator matching (no program-ID binding) - (File: universalClient/chains/svm/event_parser.go / event_listener.go)

### Summary
`EventListener.determineEventType` classifies Solana `"Program data: "` log lines as trusted gateway events purely by matching a public, deterministic 8-byte discriminator, without verifying the log was emitted by the configured gateway program. Combined with `GetSignaturesForAddress` returning transactions that merely reference the gateway account, an attacker can craft a transaction from their own program that emits a forged log with a valid discriminator, causing the universal client to parse and vote on entirely fabricated inbound/outbound events.

### Finding Description
`determineEventType` (event_listener.go:398-423) and `ParseEvent`'s handlers (event_parser.go) trust any log matching a known discriminator regardless of which program emitted it, and the discovery mechanism (`GetSignaturesForAddress`) does not guarantee the gateway program actually executed in the transaction — only that its account is referenced. No correlation is performed against `Program <gatewayAddr> invoke`/`success` bracketing lines to bind the event to genuine gateway execution.

### Impact Explanation
An attacker-controlled program can inject forged `send_funds`, `finalize_universal_tx`, `revert_universal_tx`, or `funds_rescued` events that honest universal validators independently observe and vote for identically, reaching quorum without any malicious validator. This can fabricate inbound deposits or corrupt outbound finalization/gas-refund state, threatening unauthorized fund creation or accounting corruption.

### Likelihood Explanation
Requires only deploying an ordinary (unprivileged) Solana program and submitting a transaction that references the gateway account while emitting a matching-discriminator log — no privileged access, validator collusion, or gateway compromise needed. Discriminators are derived from public, non-secret gateway method names/config.

### Recommendation
Bind event acceptance to actual gateway program provenance: parse the structured Solana log frames (`Program <id> invoke [depth]` / `Program data:` / `Program <id> success`) and only accept a `"Program data: "` line that falls within an active invocation frame of `el.gatewayAddress`, rejecting any data line emitted by a different program ID, even within the same transaction.

### Proof of Concept
1. Attacker deploys program `X` and builds a transaction that includes the real gateway program's pubkey as a (non-invoked, e.g. readonly) account and also invokes program `X`.
2. Program `X` emits `sol_log_data` with bytes `discriminator(finalize_universal_tx) || forged_tx_id || forged_universal_tx_id || gas_fee || gas_used || ...` matching the 97-byte minimum layout expected by `parseOutboundObservationEvent`.
3. `GetSignaturesForAddress(gatewayAddr)` returns this signature since the gateway account is referenced.
4. `processSignatureBatch` iterates all `tx.Meta.LogMessages`, `determineEventType` matches the discriminator, and `ParseEvent` accepts the forged payload as a genuine outbound observation, which is then confirmed and voted on by honest universal validators via `MsgVoteOutbound`/`MsgVoteInbound`.

### Citations

**File:** universalClient/chains/svm/event_listener.go (L74-88)
```go
	// Build discriminator to event type mapping
	discriminatorToEventType := make(map[string]string)
	for _, method := range gatewayMethods {
		if method.EventIdentifier == "" {
			continue
		}
		switch method.Name {
		case EventTypeSendFunds,
			EventTypeFinalizeUniversalTx,
			EventTypeRevertUniversalTx,
			EventTypeFundsRescued:
			discriminator := strings.ToLower(method.EventIdentifier)
			discriminatorToEventType[discriminator] = method.Name
		}
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

**File:** universalClient/chains/svm/event_confirmer.go (L149-172)
```go
		// Get transaction
		tx, err := ec.rpcClient.GetTransaction(ctx, sig)
		if err != nil {
			// Transaction not found or not yet confirmed - skip
			continue
		}

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

**File:** universalClient/chains/common/event_processor.go (L114-150)
```go
// processConfirmedEvents processes confirmed events (both inbound and outbound)
func (ep *EventProcessor) processConfirmedEvents(ctx context.Context) error {
	events, err := ep.chainStore.GetConfirmedEvents(1000)
	if err != nil {
		return fmt.Errorf("failed to get confirmed events: %w", err)
	}

	for _, event := range events {
		if event.Type == store.EventTypeInbound {
			if !ep.inboundEnabled {
				ep.logger.Warn().Str("event_id", event.EventID).Msg("inbound disabled, skipping inbound event processing")
				continue
			}
			if err := ep.processInboundEvent(ctx, &event); err != nil {
				ep.logger.Error().
					Err(err).
					Str("event_id", event.EventID).
					Msg("failed to vote on inbound event")
				continue
			}
		} else if event.Type == store.EventTypeOutbound {
			if !ep.outboundEnabled {
				ep.logger.Warn().Str("event_id", event.EventID).Msg("outbound disabled, skipping outbound event processing")
				continue
			}
			if err := ep.processOutboundEvent(ctx, &event); err != nil {
				ep.logger.Error().
					Err(err).
					Str("event_id", event.EventID).
					Msg("failed to vote on outbound event")
				continue
			}
		}
	}

	return nil
}
```

**File:** universalClient/chains/common/event_processor.go (L198-218)
```go
// processInboundEvent processes an inbound event by voting on it and confirming it
func (ep *EventProcessor) processInboundEvent(ctx context.Context, event *store.Event) error {
	ep.logger.Debug().
		Str("event_id", event.EventID).
		Msg("processing inbound event")

	// Extract inbound data from event
	inbound, err := ep.constructInbound(event)
	if err != nil {
		return fmt.Errorf("failed to construct inbound: %w", err)
	}

	// Execute vote on blockchain
	voteTxHash, err := ep.signer.VoteInbound(ctx, inbound)
	if err != nil {
		ep.logger.Error().
			Str("event_id", event.EventID).
			Err(err).
			Msg("failed to vote on event - keeping status for retry")
		return err
	}
```

**File:** x/uexecutor/keeper/msg_vote_inbound.go (L18-70)
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

	// use a temporary context to not commit any ballot state change in case of error
	tmpCtx, commit := sdkCtx.CacheContext()

	// Step 2: Record this validator's vote in the per-utx PendingInbounds entry
	// (variant-aware audit trail). Each unique Inbound payload becomes its own
	// variant; multiple variants per utx_key indicate validator divergence.
	ballotKey, err := types.GetInboundBallotKey(inbound)
	if err != nil {
		return errors.Wrap(err, "failed to derive inbound ballot key")
	}
	if err := k.RecordInboundVote(tmpCtx, inbound, universalValidator.String(), ballotKey); err != nil {
		return err
	}

	// Step 3: Vote on inbound ballot (uses the original inbound data as-is for the ballot key,
	// so UVs that observe different field data will correctly produce different votes)
	isFinalized, _, err := k.VoteOnInboundBallot(tmpCtx, universalValidator, inbound)
```

**File:** x/uexecutor/keeper/voting.go (L11-70)
```go
func (k Keeper) VoteOnInboundBallot(
	ctx context.Context,
	universalValidator sdk.ValAddress,
	inbound types.Inbound,
) (isFinalized bool,
	isNew bool,
	err error) {
	ballotKey, err := types.GetInboundBallotKey(inbound)
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

	k.Logger().Debug("voting on inbound ballot",
		"ballot_key", ballotKey,
		"validator", universalValidator.String(),
		"total_validators", totalValidators,
		"votes_needed", votesNeeded,
	)

	// Convert []sdk.ValAddress → []string
	universalValidatorSetStrs := make([]string, len(universalValidatorSet))
	for i, v := range universalValidatorSet {
		universalValidatorSetStrs[i] = v.IdentifyInfo.CoreValidatorAddress
	}

	// Step 2: Call VoteOnBallot for this inbound synthetic
	_, isFinalized, isNew, err = k.uvalidatorKeeper.VoteOnBallot(
		ctx,
		ballotKey,
		uvalidatortypes.BallotObservationType_BALLOT_OBSERVATION_TYPE_INBOUND_TX,
		universalValidator.String(),
		uvalidatortypes.VoteResult_VOTE_RESULT_SUCCESS,
		universalValidatorSetStrs,
		int64(votesNeeded),
		int64(types.DefaultExpiryAfterBlocks),
	)
	if err != nil {
		return false, false, err
	}

	if isNew {
		k.Logger().Debug("inbound ballot created", "ballot_key", ballotKey)
	}
	if isFinalized {
		k.Logger().Info("inbound ballot finalized", "ballot_key", ballotKey, "source_chain", inbound.SourceChain)
	}

	return isFinalized, isNew, nil
```
