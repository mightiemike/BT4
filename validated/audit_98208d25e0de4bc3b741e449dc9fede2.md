## Analysis

`processBlockChunk` in `universalClient/chains/evm/event_listener.go` fetches raw EVM logs for the gateway/vault addresses and topics, resolves the event type from `topicToEventType`, and calls `ParseEvent` [1](#0-0) . For `sendFunds` events, `ParseEvent → parseSendFundsEvent → parseUniversalTxEvent → parseUniversalTx → finalizeEvent` decodes the event payload directly from raw ABI-encoded log data supplied by the source-chain gateway contract call [2](#0-1) .

The critical fact: `payload.TxType` is read from Word 4 of the log data, which is fully attacker-controlled input to the gateway's `sendFunds` call, and `finalizeEvent` uses it to select the confirmation policy with no other validation:

```go
if payload.TxType == 0 || payload.TxType == 1 {
    event.ConfirmationType = store.ConfirmationFast
} else {
    event.ConfirmationType = store.ConfirmationStandard
}
``` [3](#0-2) 

This `ConfirmationType` is later used by `EventConfirmer.getRequiredConfirmations` to decide how many block confirmations (`fastConfirmations` vs `standardConfirmations`, e.g. defaulting to 5 vs 12) are required before the event transitions from `Pending` to `Confirmed` and becomes eligible for downstream voting/finalization: [4](#0-3) [5](#0-4) .

Because `TxType` is chosen entirely by the unprivileged caller of the source-chain gateway's `sendFunds`, and the confirmation-depth decision has no correlation to the deposited `amount`, `token`, or any independent risk assessment, an attacker can submit an arbitrarily large-value deposit while forcing `TxType` into the "fast" bucket (0 or 1), causing the universal client to require only the (lower) fast-confirmation depth instead of the standard depth before treating the event as final and reachable by voting/finalization. This weakens the finality assumption the confirmation-tiering mechanism is meant to provide, and on any source chain where a reorg past the fast-confirmation depth is feasible, an attacker could get a large deposit "confirmed" and voted/finalized (potentially minting/crediting funds on Push Chain) and then have the underlying source-chain transfer reorganized away — a classic shallow-reorg double-spend enabled purely by attacker-chosen event fields, not by any external validator or relayer misbehavior.

This does satisfy the "no privileged actor" requirement (only an ordinary `sendFunds` caller is needed) and lands in the in-scope surface (`universalClient/chains/evm/event_parser.go`, `event_confirmer.go`, `event_listener.go`).

### Title
Attacker-controlled `TxType` field lets unprivileged depositors self-select weak confirmation policy, enabling reorg double-spend of bridge deposits - (File: universalClient/chains/evm/event_parser.go)

### Summary
The EVM `sendFunds` event parser derives the event's confirmation policy (`ConfirmationFast` vs `ConfirmationStandard`) solely from the caller-supplied `TxType` field embedded in the gateway log data, with no cross-check against deposit `amount`, `token`, or chain-specific reorg risk.

### Finding Description
`parseUniversalTx`/`finalizeEvent` decode `TxType` from Word 4 of the ABI-encoded `sendFunds` log payload — a value fully controlled by the unprivileged transaction sender calling the gateway contract — and use it directly to pick the number of required confirmations via `EventConfirmer.getRequiredConfirmations`. [6](#0-5)  There is no invariant tying the "fast" classification to safe conditions (e.g., small amount, specific token, or independently-verified risk tier); the classification is a raw pass-through of attacker input into the security-relevant decision of "how many confirmations are required before this event is treated as final and eligible for downstream voting/finalization." [4](#0-3) 

### Impact Explanation
If an attacker deposits a large amount while forcing `TxType=0/1`, the event only needs `fastConfirmations` (default 5) blocks instead of `standardConfirmations` (default 12) to be marked `Confirmed` and pushed toward voting/finalization. On chains where a reorg deeper than the fast threshold is achievable, the attacker can get Push Chain to credit/mint/execute against a deposit that is subsequently reverted on the source chain via reorg, producing unauthorized minted value / draining of bridge-backed funds without a corresponding locked deposit — satisfying the "unauthorized mint" / "draining" impact class.

### Likelihood Explanation
Exploitation requires the attacker to also achieve a chain reorg deep enough to exceed the fast-confirmation threshold on the specific source chain configured for the gateway; this is chain-dependent and not always practical on well-secured chains, but the underlying flaw — letting an unprivileged depositor unilaterally choose their own confirmation/finality tier independent of value at risk — is fully attacker-triggerable today and is a genuine violation of the intended "one event → one confirmation policy determined by protocol risk, not by the depositor" invariant.

### Recommendation
Do not let `TxType` alone determine `ConfirmationType`. Either (a) fix the confirmation tier per event *kind* (e.g., always Standard for `sendFunds`/inbound deposits) rather than a caller-supplied enum, or (b) bound the fast tier to a maximum deposit amount/token allowlist validated independently of attacker input, and reject/downgrade any `TxType` value that does not match an expected, gateway-enforced encoding.

### Proof of Concept
1. Attacker calls the source-chain gateway's `sendFunds` with `TxType=0` (or `1`) and a large `amount`/`token`.
2. `parseUniversalTx`/`finalizeEvent` set `event.ConfirmationType = ConfirmationFast` purely from this attacker-controlled field. [3](#0-2) 
3. After `fastConfirmations` blocks, `EventConfirmer` marks the event `Confirmed` and it becomes eligible for voting/finalization on Push Chain. [7](#0-6) 
4. Attacker (or colluding miner/validator on the source chain, outside Push Chain's control) reorgs the source chain past the fast-confirmation depth, reverting the underlying deposit while Push Chain has already acted on the confirmed event.

### Citations

**File:** universalClient/chains/evm/event_listener.go (L298-309)
```go
	for _, log := range logs {
		if len(log.Topics) == 0 {
			continue
		}

		// Determine event type based on topic
		eventType, ok := el.topicToEventType[log.Topics[0]]
		if !ok {
			continue
		}

		event := ParseEvent(&log, eventType, el.chainID, el.logger)
```

**File:** universalClient/chains/evm/event_parser.go (L235-239)
```go
	if payload.TxType == 0 || payload.TxType == 1 {
		event.ConfirmationType = store.ConfirmationFast
	} else {
		event.ConfirmationType = store.ConfirmationStandard
	}
```

**File:** universalClient/chains/evm/event_parser.go (L242-280)
```go
/*
UniversalTx Event (V2 - upgraded chains):
  - sender (address, indexed)
  - recipient (address, indexed)
  - token (address)             — Word 0
  - amount (uint256)            — Word 1
  - payload (bytes)             — Word 2 (offset)
  - revertRecipient (address)   — Word 3
  - txType (TX_TYPE)            — Word 4
  - signatureData (bytes)       — Word 5 (offset)
  - fromCEA (bool)              — Word 6
*/
func parseUniversalTx(event *store.Event, log *types.Log, dataOffset uint64, payload *common.UniversalTx, logger zerolog.Logger) {
	data := log.Data

	decodePayload(data, dataOffset, payload, logger)

	// revertRecipient (plain address at Word 3)
	if w := readWord(data, 3); w != nil {
		payload.RevertFundRecipient = ethcommon.BytesToAddress(w[12:32]).Hex()
	}

	// txType (Word 4)
	if w := readWord(data, 4); w != nil {
		payload.TxType = uint(new(big.Int).SetBytes(w).Uint64())
	}

	// signatureData (Word 5 offset)
	if w := readWord(data, 5); w != nil {
		payload.VerificationData = decodeSignatureData(data, w, uint64(32*7))
	}

	// fromCEA (Word 6)
	if w := readWord(data, 6); w != nil {
		payload.FromCEA = new(big.Int).SetBytes(w).Uint64() != 0
	}

	finalizeEvent(event, payload, logger)
}
```

**File:** universalClient/chains/evm/event_confirmer.go (L161-226)
```go
		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1

		if confirmations >= requiredConfirmations {
			var rowsAffected int64

			// For outbound events, enrich with gas fee before confirming
			if event.Type == store.EventTypeOutbound {
				tx, _, txErr := ec.rpcClient.GetTransactionByHash(ctx, hash)
				if txErr != nil {
					ec.logger.Warn().
						Err(txErr).
						Str("event_id", event.EventID).
						Str("tx_hash", txHash).
						Msg("failed to fetch transaction for gas fee, skipping confirmation")
					continue
				}
				gasUsed := new(big.Int).SetUint64(receipt.GasUsed)
				gasPrice := tx.GasPrice()
				gasFeeUsed := new(big.Int).Mul(gasUsed, gasPrice).String()

				// Unmarshal, set GasFeeUsed, re-marshal
				var outboundEvent chaincommon.OutboundEvent
				if unmarshalErr := json.Unmarshal(event.EventData, &outboundEvent); unmarshalErr != nil {
					ec.logger.Error().
						Err(unmarshalErr).
						Str("event_id", event.EventID).
						Msg("failed to unmarshal outbound event data")
					continue
				}
				outboundEvent.GasFeeUsed = gasFeeUsed

				updatedData, marshalErr := json.Marshal(outboundEvent)
				if marshalErr != nil {
					ec.logger.Error().
						Err(marshalErr).
						Str("event_id", event.EventID).
						Msg("failed to marshal enriched outbound event data")
					continue
				}

				rowsAffected, err = ec.chainStore.UpdateStatusAndEventData(event.EventID, store.StatusPending, store.StatusConfirmed, updatedData)
			} else {
				rowsAffected, err = ec.chainStore.UpdateEventStatus(event.EventID, store.StatusPending, store.StatusConfirmed)
			}

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

**File:** universalClient/chains/evm/event_confirmer.go (L248-267)
```go
// getRequiredConfirmations returns the required number of confirmations based on confirmation type
func (ec *EventConfirmer) getRequiredConfirmations(confirmationType string) uint64 {
	switch confirmationType {
	case store.ConfirmationFast:
		if ec.fastConfirmations >= 0 {
			return ec.fastConfirmations
		}
		return 5
	case store.ConfirmationStandard:
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	default:
		// Default to standard if unknown
		if ec.standardConfirmations >= 0 {
			return ec.standardConfirmations
		}
		return 12
	}
```
