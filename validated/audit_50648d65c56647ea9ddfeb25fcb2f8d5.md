### Title
Unchecked `uint64` subtraction in event-confirmation math can cause premature confirmation of unconfirmed inbound/outbound transactions - (File: `universalClient/chains/evm/event_confirmer.go`)

### Summary
The external report flags Solidity 0.8's checked arithmetic causing unwanted reverts on *intended* overflow/underflow. The inverse failure mode is the real risk in this Go codebase: Go performs **no runtime checks** on unsigned integer arithmetic, so any `uint64` subtraction that isn't guarded by an explicit `>=`/`>` comparison silently wraps to a huge value instead of erroring. `EventConfirmer.processPendingEvents` computes confirmation counts with exactly this unguarded pattern.

### Finding Description
`processPendingEvents` computes:
```go
confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1
``` [1](#0-0) 

Unlike the sibling function `VerifyBroadcastedTx`, which explicitly guards this exact computation with `if err == nil && latestBlock >= receiptBlock`, [2](#0-1)  there is no such guard here. If `latestBlock < receipt.BlockNumber.Uint64()` (e.g., the polled node is momentarily behind the block the transaction was mined in, a light reorg, or RPC-endpoint inconsistency between successive calls), the `uint64` subtraction underflows and wraps around to a value near `math.MaxUint64`. The subsequent check:
```go
if confirmations >= requiredConfirmations {
``` [3](#0-2) 
will then always be true, causing the event to be treated as fully confirmed — regardless of the configured `fastConfirmations`/`standardConfirmations` requirement [4](#0-3) .

### Impact Explanation
Premature confirmation defeats the entire purpose of the confirmation wait: it lets an inbound event that has not accumulated the required number of confirmations get promoted straight to `CONFIRMED` and enter the voting/processing pipeline (`GetNonExpiredConfirmedEvents` → ballot voting → `UniversalTx` minting) [5](#0-4) . If the observed transaction is later reorged out, the chain will have already voted/minted based on an event that never became final, corrupting inbound accounting. This is a state-safety/consensus-integrity risk in the universal execution ingestion path, not a purely local one, since all Universal Validators run this same node code and would independently reach the same "wrap-around" false-confirmation under the same RPC lag condition, producing wrong finalized state.

### Likelihood Explanation
The trigger condition (`latestBlock < receipt.BlockNumber`) is not attacker-forged data — it depends on the local RPC node's block-height view momentarily lagging behind the block containing the receipt, which can happen during ordinary chain reorgs, RPC load-balancer inconsistency, or two sequential RPC calls hitting nodes at different sync heights. This is a naturally-occurring race rather than something a remote unprivileged attacker can trivially or reliably force, which weighs against high likelihood; however, it requires no privileged access and no malicious validator/peer to occur, and the impact scope (accepting insufficiently-confirmed inbound events) is directly relevant to the "Honest-validator finalization path" invariant in the allowed-impact gate.

### Recommendation
Guard the subtraction exactly as `VerifyBroadcastedTx` already does:
```go
if latestBlock < receipt.BlockNumber.Uint64() {
    continue // not yet confirmed / possible reorg — wait for next poll
}
confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1
```
More generally, audit all unsigned (`uint64`) subtractions across `universalClient/` and `x/` for missing bounds checks before subtracting, consistent with the Solidity lesson that arithmetic which can validly go negative/underflow must be explicitly handled rather than left to default language behavior (in Go's case, silent wraparound rather than a revert).

### Proof of Concept
1. A transaction included in block `N` on the source chain is recorded with `event.BlockHeight`/`receipt.BlockNumber = N`.
2. `EventConfirmer.processPendingEvents` calls `GetLatestBlock` immediately after, hitting an RPC node whose head is momentarily at block `N-1` (any brief reorg, load-balanced RPC pool with slightly stale replicas, or WS/HTTP race), so `latestBlock = N-1`.
3. `confirmations := (N-1) - N + 1` underflows in `uint64` arithmetic to `18446744073709551615` (2⁶⁴-1) instead of `0`.
4. `confirmations >= requiredConfirmations` (e.g., `12`) is trivially true, and the event is immediately marked `CONFIRMED` [6](#0-5)  despite having zero real confirmations, allowing it to proceed to voting/minting before the source chain has actually finalized it.

### Citations

**File:** universalClient/chains/evm/event_confirmer.go (L161-163)
```go
		// Check if transaction is confirmed based on confirmation type
		requiredConfirmations := ec.getRequiredConfirmations(event.ConfirmationType)
		confirmations := latestBlock - receipt.BlockNumber.Uint64() + 1
```

**File:** universalClient/chains/evm/event_confirmer.go (L165-203)
```go
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

**File:** universalClient/chains/evm/tx_builder.go (L259-263)
```go
	var confs uint64
	latestBlock, err := tb.rpcClient.GetLatestBlock(ctx)
	if err == nil && latestBlock >= receiptBlock {
		confs = latestBlock - receiptBlock + 1
	}
```

**File:** universalClient/tss/eventstore/store.go (L189-210)
```go
// GetNonExpiredConfirmedEvents returns confirmed events ready to be processed.
// Events must be at least minBlockConfirmation blocks old and not past expiry.
// expiry_block_height = 0 means "no client-side expiry" and matches always.
func (s *Store) GetNonExpiredConfirmedEvents(currentBlock, minBlockConfirmation uint64, limit int) ([]store.Event, error) {
	var minBlock uint64
	if currentBlock > minBlockConfirmation {
		minBlock = currentBlock - minBlockConfirmation
	}

	query := s.db.Where("status = ? AND block_height <= ? AND (expiry_block_height = 0 OR expiry_block_height > ?)",
		store.StatusConfirmed, minBlock, currentBlock).
		Order("block_height ASC, created_at ASC")
	if limit > 0 {
		query = query.Limit(limit)
	}

	var events []store.Event
	if err := query.Find(&events).Error; err != nil {
		return nil, fmt.Errorf("failed to query confirmed events: %w", err)
	}
	return events, nil
}
```
