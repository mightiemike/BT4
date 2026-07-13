The key question is whether `processEventsAndClaimBonus` advances checkpoints **before** the `sufficientBonusPoolBalance` check, and whether those advanced checkpoints are then persisted via `setPositionWithState` even when `ErrInsufficientBonusPool` is returned. Let me trace the exact code.

The invariant break is real and concrete. Here is the exact trace:

**Step 1 — Checkpoints are mutated BEFORE the pool balance check in `processEventsAndClaimBonus`:**

Inside the event loop (lines 172–198), `pos.UpdateLastEventSeq(entry.Seq)` is called for every event, and `k.decrementEventRefCount` writes to the store. After the loop, `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` (line 215) and `pos.UpdateLastKnownBonded` updates bonded state (line 217). Only then, at line 230, does `sufficientBonusPoolBalance` run. If it fails, `ErrInsufficientBonusPool` is returned with `pos` already carrying fully-advanced `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded`.

**Step 2 — `slashRedelegationPosition` ignores the error and persists the advanced checkpoints:**

The `ErrInsufficientBonusPool` branch at lines 56–63 logs the error and falls through. Execution reaches line 77 (`setPositionWithState(ctx, pos, nil)`) unconditionally, persisting the `pos` struct whose checkpoints were already advanced by `processEventsAndClaimBonus`.

**Step 3 — Future claims skip the slash-period events:**

`processEventsAndClaimBonus` starts replay from `pos.LastEventSeq` (line 153: `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)`). Because that seq was advanced during the failed slash call, the events covering the slash period are never replayed again. The bonus for that period is permanently unclaimable.

**Step 4 — Event reference counts are also decremented without payment:**

`decrementEventRefCount` is called inside the loop (line 196) before the pool check. Those store writes are not rolled back. Events may be garbage-collected, making the skipped period structurally unrecoverable even if the pool is later refilled.

---

### Title
Checkpoint Advancement Without Bonus Payment in `slashRedelegationPosition` Causes Permanent Loss of Bonus Rewards — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

### Summary
When `slashRedelegationPosition` calls `processEventsAndClaimBonus` and the bonus pool is empty, the function advances the position's `LastEventSeq`, `LastBonusAccrual`, and `LastKnownBonded` checkpoints in-memory **before** checking pool sufficiency. The `ErrInsufficientBonusPool` error is caught and silenced in `slashRedelegationPosition`, which then calls `setPositionWithState` with the already-advanced checkpoints. All future bonus claims start from those advanced checkpoints, permanently skipping the slash-period events and forfeiting the bonus owed for that period.

### Finding Description
In `processEventsAndClaimBonus` (`claim_rewards.go`):

- The event loop (lines 172–198) calls `pos.UpdateLastEventSeq(entry.Seq)` and `k.decrementEventRefCount` for every event. [1](#0-0) 
- `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` run unconditionally after the loop. [2](#0-1) 
- Only then does `sufficientBonusPoolBalance` execute (line 230). If it returns `ErrInsufficientBonusPool`, the function returns with `pos` already mutated. [3](#0-2) 

In `slashRedelegationPosition` (`slash.go`):

- The `ErrInsufficientBonusPool` branch logs the error and falls through without resetting `pos`. [4](#0-3) 
- `setPositionWithState(ctx, pos, nil)` is then called unconditionally, persisting the advanced checkpoints. [5](#0-4) 

The broken invariant: **if bonus is not paid, the position's checkpoints must not be advanced**. The code violates this because checkpoint mutation and payment are not atomic — mutation happens first, payment happens last.

### Impact Explanation
Any position with an active redelegation that is slashed while the bonus pool is empty permanently loses the bonus rewards accrued up to the slash event. The loss is irreversible: the events are skipped on all future `processEventsAndClaimBonus` calls because `LastEventSeq` already points past them, and `decrementEventRefCount` may have already garbage-collected those events from the store.

### Likelihood Explanation
The bonus pool can be empty transiently (e.g., between funding cycles, or if a large batch of claims drains it). Redelegation slashes occur whenever a validator double-signs while a redelegation is in its unbonding window. The two conditions can coincide without any attacker involvement; a malicious actor could also deliberately drain the pool before a known slash event.

### Recommendation
Move `sufficientBonusPoolBalance` (and the `SendCoinsFromModuleToAccount` call) **before** any mutation of `pos` checkpoints, or snapshot the original checkpoint values and restore them on `ErrInsufficientBonusPool`. The simplest fix is to compute `totalBonus` and check pool sufficiency first, then replay the event loop a second time to advance checkpoints only after confirming payment will succeed. Alternatively, use a cache context for the entire `processEventsAndClaimBonus` call so that store writes (including `decrementEventRefCount`) are rolled back on error.

### Proof of Concept
1. Create a position with a redelegation; record `LastEventSeq = S` and `LastBonusAccrual = T`.
2. Drain the bonus pool to zero.
3. Trigger a validator slash that fires `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
4. Observe that the persisted position now has `LastEventSeq > S` and `LastBonusAccrual > T`, but no bonus was transferred.
5. Refill the bonus pool.
6. Call `ClaimTierRewards`; observe that `processEventsAndClaimBonus` starts from the advanced `LastEventSeq`, skipping the slash-period events, and the bonus for that period is never paid. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-232)
```go
	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-77)
```go
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** x/tieredrewards/types/position.go (L98-104)
```go
func (p *Position) UpdateLastEventSeq(seq uint64) {
	p.LastEventSeq = seq
}

func (p *Position) UpdateLastKnownBonded(bonded bool) {
	p.LastKnownBonded = bonded
}
```
