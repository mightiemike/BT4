## Title
Missing progress guarantee in `processBroadcasted`'s pagination loop causes livelock and indefinite starvation of pending outbound/fund-migration resolutions — (`File: universalClient/tss/txresolver/resolver.go`)

## Summary
`processBroadcasted` repeatedly re-queries the same ordered, un-offset page of `BROADCASTED` events until fewer than `processBroadcastedBatchSize` (100) rows are returned. If ≥100 events remain in `BROADCASTED` status without a state change within an iteration (e.g. all are still below the required confirmation threshold, or their RPC verification errors/defers), the query returns the identical top-N rows forever, and the function never advances past them — it either livelocks (never returns, permanently hanging the resolver goroutine) or, at minimum, indefinitely starves every event ordered after the stuck front batch, including a victim's specific REVERT/COMPLETED resolution.

## Finding Description
`GetBroadcastedSignEvents` orders rows deterministically by `block_height ASC, created_at ASC` and applies only `LIMIT`, with no `OFFSET` or cursor: [1](#0-0) 

`processBroadcasted` loops on this query, and only stops fetching more pages once a page returns fewer than `processBroadcastedBatchSize` (100) rows: [2](#0-1) 

`resolveOutbound`/`resolveOutboundEVM` leave an event's status unchanged as `BROADCASTED` in several common, non-malicious conditions — e.g. insufficient confirmations, RPC verify errors, or missing nonce/TSS-address context — deferring resolution to "next tick" (confirmed by tests such as `TestResolveOutboundEVM_InsufficientConfirmations_StaysBroadcasted` and `TestResolveOutboundEVM_VerifyError_StaysBroadcasted`).

Because the query has no offset, if the oldest 100 `BROADCASTED` rows (by `block_height`/`created_at`) all stay unresolved within a call, the inner `for` loop in `processBroadcasted` re-fetches the *exact same* 100 rows every iteration (since `len(events) == 100 == processBroadcastedBatchSize` never triggers the `return`), and none of them can be replaced by newer/victim rows further down the order. This is either:
1. An outright livelock — the function never returns, blocking `run()`'s select loop (`ctx.Done()` is checked only between ticks, not inside `processBroadcasted`), permanently halting all further resolution across every chain, or
2. If external state eventually changes (confirmations accrue over real block time) — the goroutine busy-spins on the same stuck front batch for the entire duration, during which any event ordered after those 100 rows (a victim's REVERT/COMPLETED resolution) is never even fetched, let alone processed.

An unprivileged user can trigger the "≥100 concurrently pending, still-unconfirmed" precondition simply by submitting many ordinary low-value deposits that are each outbound-eligible, causing the node to broadcast ≥100 outbound transactions in a short window. Since confirmations for a freshly broadcast transaction start at 0/low and the required confirmation threshold takes real block time to satisfy, this condition is trivially reachable through the standard deposit → outbound-broadcast flow, without any privileged actor.

Because every honest universal validator/relayer runs identical `universalClient` code against the same source-chain conditions, all honest nodes are likely to hit this simultaneously, halting outbound REVERT/COMPLETED voting network-wide for the duration of the stall — a genuine, non-network-level liveness failure reachable purely from ordinary user transaction volume.

## Impact Explanation
Funds tied to a victim's outbound transaction (and potentially all in-flight outbound/fund-migration transactions) cannot progress to REVERT or COMPLETED while the resolver is stuck reprocessing the attacker-inflated front of the queue. In the livelock case, this is a total, indefinite halt of the resolver goroutine for that node (and, since it applies identically to all honest UVs, network-wide), not merely delayed processing of a single victim's event — this directly matches and exceeds the "freezing funds" impact described in the question.

## Likelihood Explanation
Reachable by an ordinary unprivileged user through the standard deposit flow: submitting ≥100 low-value outbound-eligible deposits in a short window is sufficient to populate the front of the `BROADCASTED` queue with events that are guaranteed to be below the confirmation threshold at query time, deterministically forcing the loop into the described stuck state. No malicious peer, validator, or TSS participant assumption is required.

## Recommendation
Make forward progress independent of per-row status changes: track already-visited event IDs/cursor (e.g. `(block_height, created_at, event_id)` keyset pagination) within a single `processBroadcasted` invocation instead of re-issuing an unconditioned `LIMIT`-only query, or cap iteration count/deadline and always advance past rows visited in the current call regardless of whether their status changed, and check `ctx.Done()` inside the loop so shutdown/cancellation can interrupt a stuck run.

## Proof of Concept
1. Insert 100 `BROADCASTED` `store.Event` rows (`Type=EventTypeSignOutbound`) with `block_height`/`created_at` earlier than a 101st "victim" event, and configure the mock `TxBuilder.VerifyBroadcastedTx` for all 100 to return `found=true` with confirmations below the required threshold (as in `TestResolveOutboundEVM_InsufficientConfirmations_StaysBroadcasted`), which leaves status `BROADCASTED` unchanged.
2. Call `resolver.processBroadcasted(ctx)` once (or run `resolver.run(ctx)` for one tick).
3. Observe: `GetBroadcastedSignEvents(100)` keeps returning the same 100 stuck rows every inner-loop iteration (verify via a call counter/mock assertion count on `VerifyBroadcastedTx`), `processBroadcasted` either never returns within a bounded number of iterations or spins indefinitely, and the victim's 101st event's status is asserted to remain `BROADCASTED` (never reached) regardless of how many ticks/iterations elapse — violating the expectation that it should be processed within a bounded number of ticks. [2](#0-1) [1](#0-0)

### Citations

**File:** universalClient/tss/eventstore/store.go (L241-254)
```go
// GetBroadcastedSignEvents returns SIGN events with status BROADCASTED (for receipt check).
func (s *Store) GetBroadcastedSignEvents(limit int) ([]store.Event, error) {
	if limit <= 0 {
		limit = 50
	}
	var events []store.Event
	if err := s.db.Where("type IN (?, ?) AND status = ? AND broadcasted_tx_hash != ?", store.EventTypeSignOutbound, store.EventTypeSignFundMigrate, store.StatusBroadcasted, "").
		Order("block_height ASC, created_at ASC").
		Limit(limit).
		Find(&events).Error; err != nil {
		return nil, fmt.Errorf("failed to query broadcasted sign events: %w", err)
	}
	return events, nil
}
```

**File:** universalClient/tss/txresolver/resolver.go (L73-95)
```go
const processBroadcastedBatchSize = 100

func (r *Resolver) processBroadcasted(ctx context.Context) {
	if r.chains == nil {
		return
	}
	for {
		events, err := r.eventStore.GetBroadcastedSignEvents(processBroadcastedBatchSize)
		if err != nil {
			r.logger.Warn().Err(err).Msg("failed to get broadcasted sign events")
			return
		}
		if len(events) == 0 {
			return
		}
		for i := range events {
			r.resolveEvent(ctx, &events[i])
		}
		if len(events) < processBroadcastedBatchSize {
			return
		}
	}
}
```
