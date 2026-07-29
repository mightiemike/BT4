## Analysis

`processInboundEvent` at [1](#0-0)  has no retry cap, no exponential backoff, and no dead-letter/quarantine mechanism: on any `signer.VoteInbound` failure it simply logs and returns the error, leaving the event's DB status unchanged.

Since the event's status is never advanced from `CONFIRMED` on failure, it remains eligible for `GetConfirmedEvents` forever: [2](#0-1)  queries `status = CONFIRMED` ordered by `created_at ASC` with a hard `LIMIT` (1000, per the caller at [3](#0-2) ). Because the ordering is FIFO by creation time and there is no per-event retry-count cap, backoff, or quarantine state, permanently-invalid events (e.g., events whose `Recipient`/`AssetAddr` fail chain-side `MsgVoteInbound` validation) sit at the head of the queue and get re-attempted every 5-second tick indefinitely (`processLoop`, [4](#0-3) ). The `continue` in `processConfirmedEvents` means legitimate events *within the same 1000-row batch* still get processed, but if an attacker accumulates ≥1000 concurrently outstanding permanently-failing `CONFIRMED` events (older by `created_at`), they will permanently crowd out newer legitimate events from ever entering the batch, stalling those legitimate inbound votes indefinitely.

### Scope/impact considerations
- This is client-side/off-chain code (`universalClient/`), which is in the audited scope.
- The trigger path is a normal user creating deposits/transactions on a source chain that produce events whose `Recipient`/`AssetAddr`/payload fail `MsgVoteInbound` validation on the Push Chain side (e.g. malformed addresses) — this requires no malicious validator, peer, or admin, only ordinary but malformed deposit submissions, which matches the "reachable through ordinary user deposits" requirement.
- This is not a network-level (p2p/consensus) DoS; it is an unbounded local processing-queue/resource issue affecting every honest UV symmetrically (since all UVs run the same client code against the same source-chain data), which the rules explicitly allow ("denial of service only when it is not network-level and is reachable without privileged control").
- Practically, to fully starve the queue an attacker must generate ≥1000 concurrently-pending permanently-invalid `CONFIRMED` inbound events (each requiring a real, though possibly cheap, source-chain transaction), so the severity depends on the cost of generating malformed-but-still-"CONFIRMED" events on the source chain — this could not be independently verified from the code alone, since the event confirmer's acceptance/validation criteria before marking an event `CONFIRMED` live in the per-chain confirmer files (not fully reviewed here).

### Title
Unbounded retry of permanently-invalid CONFIRMED inbound events starves legitimate vote processing (no cap/backoff/quarantine) - (universalClient/chains/common/event_processor.go)

### Summary
`EventProcessor.processInboundEvent` retries a failing `signer.VoteInbound` call forever with no cap, backoff, or terminal "invalid" state, and `ChainStore.GetConfirmedEvents` always returns the oldest `CONFIRMED` events first up to a fixed limit of 1000 per tick. An attacker who causes enough permanently-invalid inbound events to reach `CONFIRMED` status (e.g. via malformed source-chain deposits whose `Recipient`/`AssetAddr` fail `MsgVoteInbound` validation) can keep those events permanently at the front of the FIFO queue, crowding out legitimate events from the per-tick batch and stalling their processing indefinitely.

### Finding Description
- `processInboundEvent` (lines 199–237) calls `signer.VoteInbound`; on error it only logs and returns the error, without marking the event invalid, incrementing a retry counter, or applying backoff ( [5](#0-4) ).
- The event's status stays `CONFIRMED`, so it is re-selected on every 5s tick by `GetConfirmedEvents`, which orders `status = CONFIRMED` events by `created_at ASC` with no skip/offset for previously-failed items ( [2](#0-1) ).
- The batch size is capped at 1000 per tick ( [3](#0-2) ), so once ≥1000 permanently-failing `CONFIRMED` events (older than a legitimate one) exist, the legitimate event never appears in a batch and is never voted on.
- There is no dead-letter/expiry mechanism visible in `ChainStore` for `CONFIRMED`-but-failing events; `DeleteTerminalEvents` only cleans up `COMPLETED`, `REORGED`, `REVERTED` events ( [6](#0-5) ), not stuck `CONFIRMED` ones.

### Impact Explanation
This is an application-level (non-network) liveness degradation: legitimate users' inbound deposits can be starved indefinitely from being voted on and finalized, since the queue is unbounded in retry count and FIFO-ordered without failure isolation. It does not corrupt consensus state, forge votes, or cause fund loss directly, but it can materially delay honest user fund crediting.

### Likelihood Explanation
Requires the attacker to generate a large number (≥1000, given the fixed 1000-row batch limit) of source-chain deposit events that reach `CONFIRMED` status but are structurally invalid for `MsgVoteInbound` (e.g., malformed recipient/asset representation). This is achievable by an unprivileged user submitting many low-cost malformed deposits on the source chain, making the likelihood non-trivial but bounded by the cost of generating that volume of source-chain transactions.

### Recommendation
- Add a bounded retry counter or exponential backoff per event, and after exceeding a threshold, transition the event to a terminal `FAILED`/`INVALID` status instead of leaving it in `CONFIRMED` forever.
- Perform basic structural validation of `Recipient`/`AssetAddr`/payload fields before/while marking an event `CONFIRMED`, so malformed events are rejected earlier rather than looping through `VoteInbound`.
- Consider separating "poison" events into a quarantine table/status so `GetConfirmedEvents` FIFO scans aren't blocked by permanently failing entries, ensuring newer legitimate events are still included in each tick's batch.

### Proof of Concept
1. Seed the events DB with N events having `status = CONFIRMED`, `created_at` older than a legitimate event, each with a `Recipient`/`AssetAddr` known to fail `MsgVoteInbound` validation on-chain (malformed hex/address format).
2. Insert 1 legitimate `CONFIRMED` event with a later `created_at`.
3. Run `processConfirmedEvents` repeatedly (simulating 5s ticks) with `N >= 1000`.
4. Observe: `GetConfirmedEvents(1000)` always returns the N failing events (oldest first), the legitimate event is excluded from every batch, `signer.VoteInbound` is invoked N times per tick forever with no backoff, and the legitimate event is never processed.

### Citations

**File:** universalClient/chains/common/event_processor.go (L94-111)
```go
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			ep.logger.Debug().Msg("context cancelled, stopping event processor")
			return
		case <-ep.stopCh:
			ep.logger.Debug().Msg("stop signal received, stopping event processor")
			return
		case <-ticker.C:
			// Fetch 1000 CONFIRMED events and process them
			if err := ep.processConfirmedEvents(ctx); err != nil {
				ep.logger.Error().Err(err).Msg("failed to process confirmed events")
			}
		}
	}
```

**File:** universalClient/chains/common/event_processor.go (L116-116)
```go
	events, err := ep.chainStore.GetConfirmedEvents(1000)
```

**File:** universalClient/chains/common/event_processor.go (L205-218)
```go
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

**File:** universalClient/chains/common/chain_store.go (L81-97)
```go
// GetConfirmedEvents fetches confirmed events ordered by creation time
func (cs *ChainStore) GetConfirmedEvents(limit int) ([]store.Event, error) {
	if cs.database == nil {
		return nil, fmt.Errorf("database is nil")
	}

	var events []store.Event
	if err := cs.database.Client().
		Where("status = ?", store.StatusConfirmed).
		Order("created_at ASC").
		Limit(limit).
		Find(&events).Error; err != nil {
		return nil, fmt.Errorf("failed to query confirmed events: %w", err)
	}

	return events, nil
}
```

**File:** universalClient/chains/common/chain_store.go (L175-194)
```go
// DeleteTerminalEvents deletes events in terminal states (COMPLETED, REVERTED, EXPIRED)
// that were updated before the given time
func (cs *ChainStore) DeleteTerminalEvents(updatedBefore any) (int64, error) {
	if cs.database == nil {
		return 0, fmt.Errorf("database is nil")
	}

	// Unscoped() = hard delete (free disk). Without it, GORM does a soft
	// delete (just sets deleted_at), which defeats the cleaner's purpose.
	res := cs.database.Client().Unscoped().
		Where("status IN ? AND updated_at < ?",
			[]string{store.StatusCompleted, store.StatusReorged, store.StatusReverted}, updatedBefore).
		Delete(&store.Event{})

	if res.Error != nil {
		return 0, fmt.Errorf("failed to delete terminal events: %w", res.Error)
	}

	return res.RowsAffected, nil
}
```
