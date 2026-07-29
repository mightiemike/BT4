Confirmed: nowhere in `parseSendFundsEvent` / `parseOutboundObservationEvent` / `processSignatureBatch` is the emitting program verified. The code only checks the string prefix `"Program data: "` and the discriminator bytes of the decoded payload — there is no check that the log line is bracketed inside `Program <gatewayAddress> invoke [..]` / `success`, and no check that any account in the transaction was actually the gateway program's *invoked* instruction.

### Title
Forged gateway events accepted via unauthenticated log-content matching (no program-origin binding) - (File: universalClient/chains/svm/event_listener.go, universalClient/chains/svm/event_parser.go)

### Summary
`EventListener.processSignatureBatch` fetches transaction signatures for the gateway address via `GetSignaturesForAddress` [1](#0-0) , then scans **every** log line of the transaction for a `"Program data: "` prefix and matches its first 8 bytes against a discriminator map, independent of which program actually emitted the line [2](#0-1) . `determineEventType` and `ParseEvent`/`parseSendFundsEvent` perform no verification that the log was emitted by the gateway program's own instruction execution — they just base64-decode the text and trust its contents wholesale, including attacker-controlled `sender`, `recipient`, `amount`, `token`, `RawPayload`, and `VerificationData` fields [3](#0-2) [4](#0-3) .

### Finding Description
On Solana, `sol_log_data` (which produces the literal `"Program data: <base64>"` line, used by Anchor's `emit!`) is a syscall callable by *any* program, not just the gateway program. `getSignaturesForAddress` indexes any transaction that merely references an address in its account-key list — a program does not need to be invoked, only present as an account (which any unprivileged actor can add for free). An attacker can therefore:
1. Deploy their own program (permissionless on Solana).
2. Build a transaction that lists the real gateway program address as a plain account (to make the tx show up under `GetSignaturesForAddress(gatewayAddr)`), and invokes their own program, which calls `sol_log_data(discriminator || arbitrary_payload)` with the exact 8-byte discriminator used for `send_funds` (public knowledge, since it's derived from `sha256("event:<Name>")` or configured via `GatewayMethods`).
3. This transaction succeeds (`tx.Meta.Err == nil`), so `EventConfirmer` will never mark it `REVERTED` [5](#0-4) .
4. The universal client's listener picks up the log purely by string content, decodes attacker-chosen bytes into a full `UniversalTx` (`sender`, `recipient`, `amount`, `token`, `RawPayload`, `VerificationData`, `FromCEA`), and stores it as a `PENDING` inbound event via `InsertEventIfNotExists` [6](#0-5) .
5. After required confirmations elapse, `EventConfirmer` promotes it to `CONFIRMED` purely based on slot depth and `tx.Meta.Err == nil`, with no re-derivation from actual gateway state [7](#0-6) .
6. `EventProcessor.processInboundEvent` then submits `MsgVoteInbound` with this forged data to Push Chain [8](#0-7) .

Because every honest Universal Validator runs the same flawed listener code against the same public Solana transaction data, they will *all* independently derive and vote for the identical forged `Inbound`, reaching the 2/3 quorum in `VoteOnInboundBallot` without any validator, node, or relayer being malicious [9](#0-8) . This satisfies the "honest-validator" constraint in the scope rules — the vulnerability is in the client-side event-authentication logic itself, not in validator behavior.

This directly enables an unprivileged attacker to fabricate arbitrary `send_funds` inbound events (attacker-chosen sender/recipient/amount/token/payload) that reach `VoteInbound` -> ballot finalization -> `ExecuteInbound`, i.e., attacker-controlled UniversalTx creation and downstream inbound execution (potential unauthorized PRC20 mint / payload execution), not merely benign DB bloat as the original question framed it.

### Impact Explanation
This exceeds a simple DoS/junk-row concern: it is a forged-inbound-event-injection vulnerability that can reach `x/uexecutor`'s inbound finalization and execution path with data entirely fabricated by an unprivileged attacker, matching the "forged... inbound... accepted through user-reachable flows with honest validators and honest nodes" impact category. Whether it culminates in actual unauthorized minting depends on `Inbound.ValidateForExecution` and downstream token/amount checks in `x/uexecutor`/`x/uregistry` (e.g., token allow-listing, liquidity caps) which were not fully traced in this review — that residual validation could reduce (but does not eliminate) the severity, since attacker still forges the `UniversalTx` record and its accounting fields even if certain payload/token combinations get rejected. I was not able to fully verify every downstream constraint in `x/uexecutor` (`ValidateForExecution`, `ExecuteInbound`, token-mapping checks) within this review; a Devin session with fuller trace of those functions would be needed to determine the precise fund-loss ceiling.

### Likelihood Explanation
High. Deploying a Solana program and crafting a transaction that references the gateway address while emitting a forged `Program data:` log via `sol_log_data` requires no special privilege and no interaction with the actual gateway program logic. The discriminator values are either derivable (`sha256("event:<Name>")[:8]`, standard Anchor convention) or discoverable from public on-chain IDL/config.

### Recommendation
Bind log parsing to actual gateway program invocation: only treat a `Program data:` log line as a genuine gateway event if it appears within the log bracket for the specific gateway program's own invocation (i.e., track `"Program <gatewayAddress> invoke [depth]"` / matching `"Program <gatewayAddress> success"` markers and only scan `Program data:` lines within that program's own invocation depth), rather than scanning the flat `LogMessages` list irrespective of source program. Additionally consider cross-checking parsed event fields (e.g., vault balance deltas, executed-instruction accounts) against actual gateway account state before accepting an inbound as valid.

### Proof of Concept
1. Deploy a minimal Solana program `Attacker` that, when invoked, calls `sol_log_data(&[discriminator_bytes, attacker_payload].concat())` where `discriminator_bytes` matches the configured `send_funds` `EventIdentifier`.
2. Build and submit a transaction with instructions: `[invoke Attacker]`, and include the real gateway program pubkey as an extra (uninvoked, readonly) account in the transaction's account list.
3. Confirm the transaction; verify it appears in `getSignaturesForAddress(gatewayAddr)`.
4. Run/observe the universal client's SVM `EventListener`: confirm it calls `determineEventType` -> matches `send_funds` -> `ParseEvent` -> `chainStore.InsertEventIfNotExists` stores a new `PENDING` inbound `store.Event` with attacker-chosen `sender/recipient/amount/token` despite the log never having been emitted by the real gateway program.
5. Observe `EventConfirmer` promote it to `CONFIRMED` after slot depth is met (transaction succeeded, `tx.Meta.Err == nil`), and `EventProcessor` submit `MsgVoteInbound` with the forged data.

### Citations

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

**File:** universalClient/chains/svm/event_listener.go (L288-326)
```go
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

**File:** universalClient/chains/svm/event_parser.go (L236-277)
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
```

**File:** universalClient/chains/svm/event_confirmer.go (L161-172)
```go
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

**File:** universalClient/chains/svm/event_confirmer.go (L181-206)
```go
		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestSlot - txSlot + 1

		if confirmations >= requiredConfirmations {
			// GasFeeUsed for outbound events is already set by the event parser from the on-chain event data
			rowsAffected, err := ec.chainStore.UpdateEventStatus(event.EventID, store.StatusPending, store.StatusConfirmed)
			if err != nil {
				ec.logger.Error().
					Err(err).
					Str("event_id", event.EventID).
					Msg("failed to update event status")
				continue
			}

			if rowsAffected > 0 {
				confirmedCount++
				ec.logger.Debug().
					Str("event_id", event.EventID).
					Str("event_type", event.Type).
					Uint64("confirmations", confirmations).
					Uint64("required_confirmations", requiredConfirmations).
					Str("confirmation_type", event.ConfirmationType).
					Msg("event marked as CONFIRMED")
			}
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

**File:** x/uexecutor/keeper/voting.go (L11-61)
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
```
