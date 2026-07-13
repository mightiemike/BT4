### Title
Bonus Reward Checkpoints Advanced Without Payment When Pool Is Insufficient During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is silently swallowed. However, `processEventsAndClaimBonus` has already advanced the position's bonus-accrual checkpoints in memory **and** decremented validator-event reference counts in the store — both before the pool-balance check. The modified position is then persisted via `setPositionWithState`. The net result is that the position's checkpoints are committed as if the full bonus was paid, while the owner receives nothing. Because the event reference counts are also decremented, the events may be garbage-collected, making the lost rewards permanently unrecoverable.

---

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` performs three categories of side-effects before it checks whether the pool has sufficient funds:

1. **Store writes** — `decrementEventRefCount` is called inside the event loop, decrementing each event's reference count directly in the KV store.
2. **In-memory checkpoint advances** — `pos.UpdateLastEventSeq`, `applyBonusAccrualCheckpoint`, and `pos.UpdateLastKnownBonded` all mutate the `*types.PositionState` pointer before the pool check.
3. **Pool balance check** — only after all of the above does `sufficientBonusPoolBalance` run; if it fails, the function returns `ErrInsufficientBonusPool`. [1](#0-0) 

In the normal user-driven paths (ClaimTierRewards, TierUndelegate, etc.) this error propagates to the message handler, the transaction is aborted, and all store writes are rolled back. The problem is in `slashRedelegationPosition`:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error is swallowed — execution continues with modified pos
    } else {
        return err
    }
}
// ...
return k.setPositionWithState(ctx, pos, ...)   // persists modified pos
``` [2](#0-1) 

Because the error is swallowed:

- The `decrementEventRefCount` store writes are **committed** (they are part of the same transaction write-cache as the parent `SlashRedelegation` call).
- The modified `pos` — with `LastBonusAccrual` advanced to `blockTime`, `LastEventSeq` advanced past all processed events, and `LastKnownBonded` updated — is **persisted** by `setPositionWithState`.
- No bonus coins are transferred to the owner.

If any event's reference count reaches zero it is garbage-collected. The position can never replay those events again, so the bonus for the affected period is permanently lost.

---

### Impact Explanation

A position owner whose delegation was redelegated via `MsgTierRedelegate` permanently loses all accrued bonus rewards for the period between their last claim and the slash event, whenever the bonus pool is insufficient at slash time. The loss is irreversible: the checkpoints are advanced, the events may be deleted, and there is no retry path. This is a direct, permanent loss of user funds proportional to the position size, the tier's `BonusApy`, and the duration since the last claim.

---

### Likelihood Explanation

The trigger requires three concurrent conditions:

1. A position has an active redelegation (the owner called `MsgTierRedelegate`).
2. The source validator is slashed during the redelegation period (double-sign or downtime).
3. The `RewardsPoolName` module account has insufficient balance to cover the computed bonus.

All three are realistic in production. Validator slashing is a routine network event. The bonus pool can be depleted if it was never funded, was drained by many concurrent claims, or was reduced by governance. The combination is not exotic.

---

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, do **not** persist the modified position state. Two safe approaches:

**Option A — Cache context:** Wrap the `processEventsAndClaimBonus` call in a cache context. On `ErrInsufficientBonusPool`, discard the cache (rolling back both the store writes and the in-memory pointer mutations are irrelevant since the pointer is local to the cache call).

**Option B — Snapshot and restore:** Snapshot the position's checkpoint fields before calling `processEventsAndClaimBonus`. On `ErrInsufficientBonusPool`, restore the snapshot before calling `setPositionWithState`, so the persisted position retains its original checkpoints and the owner can retry later.

Either approach ensures that the position's checkpoints are only advanced when the bonus is actually paid — matching the invariant enforced in all other code paths.

---

### Proof of Concept

1. Owner calls `MsgTierRedelegate` on position P (src → dst validator). A `RedelegationMapping` entry is created.
2. The src validator is slashed. `BeforeRedelegationSlashed` fires → `slashRedelegationPosition` is called.
3. `processEventsAndClaimBonus` runs: it decrements event reference counts in the store and advances `pos.LastBonusAccrual` / `pos.LastEventSeq` in memory. It then calls `sufficientBonusPoolBalance` and finds the pool empty → returns `ErrInsufficientBonusPool`.
4. `slashRedelegationPosition` swallows the error and calls `setPositionWithState(ctx, pos, nil)`, persisting the advanced checkpoints.
5. The transaction commits. The event reference counts are now decremented in the committed store; if any reach zero, those events are deleted.
6. Owner later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the now-advanced `LastEventSeq` and `LastBonusAccrual` — the period that was silently skipped is gone. The owner receives zero bonus for that period even after the pool is replenished. [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** x/tieredrewards/keeper/slash.go (L19-77)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

	dstValStr := pos.Delegation.ValidatorAddress

	// Settle bonus against PRE-slash shares.
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

**File:** x/tieredrewards/keeper/hooks.go (L125-130)
```go
// BeforeRedelegationSlashed fires before SDK's Unbond in SlashRedelegation.
// Routes to slashRedelegationPosition via the unbondingId → positionId mapping
// so bonus settlement can run against pre-slash shares.
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
