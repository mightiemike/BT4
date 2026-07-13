### Title
Bonus Rewards Permanently Forfeited When Pool Is Insufficient During Redelegation Slash — (File: `x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is intentionally swallowed to prevent a chain halt. However, by the time the error is returned, `processEventsAndClaimBonus` has already (a) decremented validator event reference counts in the **persistent store** and (b) advanced the position's reward checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) in memory. The caller then persists the position with those advanced checkpoints. The result is that the position owner's accrued bonus rewards for the period up to the slash are permanently and irrecoverably forfeited — not deferred.

---

### Finding Description

`slashRedelegationPosition` is invoked by the `BeforeRedelegationSlashed` staking hook whenever a redelegation entry belonging to a tier position is slashed. Its purpose is to settle bonus rewards against pre-slash shares before the staking module reduces them.

**Step 1 — `processEventsAndClaimBonus` mutates state before the pool check.**

Inside `processEventsAndClaimBonus`, the event-processing loop runs first:

```go
for _, entry := range events {
    ...
    pos.UpdateLastEventSeq(entry.Seq)          // in-memory checkpoint advance
    if err := k.decrementEventRefCount(...); err != nil {  // STORE mutation
        return nil, err
    }
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // in-memory checkpoint advance
pos.UpdateLastKnownBonded(bonded)                       // in-memory checkpoint advance
```

Only after all of this does the pool balance check occur:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // ErrInsufficientBonusPool
}
``` [1](#0-0) 

**Step 2 — The error is swallowed in `slashRedelegationPosition`.**

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
    } else {
        return err
    }
}
``` [2](#0-1) 

**Step 3 — The position with advanced checkpoints is persisted.**

For a partial slash, execution continues to:

```go
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)
``` [3](#0-2) 

`pos` now carries the advanced `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` values from the failed `processEventsAndClaimBonus` call. These are written to the store. The validator events whose reference counts were already decremented may have been garbage-collected.

**Step 4 — The user can never recover the lost bonus.**

On the next `MsgClaimTierRewards`, `processEventsAndClaimBonus` starts from `pos.LastBonusAccrual = slashBlockTime` and `pos.LastEventSeq = latestSeqAtSlash`. The accrual period `[old_LastBonusAccrual, slashBlockTime]` is gone — the checkpoints have been advanced past it and the events have been decremented. There is no stored "pending unpaid bonus" amount. [4](#0-3) 

---

### Impact Explanation

A position owner permanently loses all bonus rewards accrued between their last claim and the moment of a redelegation slash, whenever the bonus pool is insufficient at slash time. The loss is not a deferral — the checkpoints are advanced and the events are garbage-collected, making the rewards irrecoverable. The corrupted value is the `LastBonusAccrual` timestamp and `LastEventSeq` on the `Position` record, which together define the lower bound of the next bonus accrual window. [5](#0-4) 

---

### Likelihood Explanation

The trigger requires two concurrent conditions: (1) a tier position is in the redelegation period (i.e., `MsgTierRedelegate` was called and the SDK redelegation has not yet matured), and (2) the bonus pool is empty or underfunded at the time the destination validator is slashed. Both conditions are reachable in normal protocol operation. The bonus pool is an externally funded module account that can be depleted; redelegation slashes are a standard Cosmos SDK slashing path. No privileged access is required — the slash is triggered by the consensus layer's evidence handling. [6](#0-5) 

---

### Recommendation

Restructure `processEventsAndClaimBonus` so that the pool balance check occurs **before** any state mutations (checkpoint advances and event reference count decrements). If the pool is insufficient, return the error without modifying `pos` or the event store. This ensures that when `slashRedelegationPosition` swallows the error, no checkpoints are advanced and no events are garbage-collected, preserving the ability to claim the bonus once the pool is replenished.

Alternatively, if the silent-forfeit design is intentional, the checkpoint advance and event reference count decrements must be skipped when the pool is insufficient, so that the position retains its pre-slash accrual state and the user can retry after the pool is replenished.

---

### Proof of Concept

1. User calls `MsgLockTier` at time T0, creating position P on validator V1. `LastBonusAccrual = T0`.
2. User calls `MsgTierRedelegate` to V2. A redelegation entry is created with `unbondingId = X`. `RedelegationMappings[X] = P.Id`.
3. Time advances to T1. Bonus accrues for period [T0, T1]. Bonus pool is empty (or becomes empty before T1).
4. V2 misbehaves. Staking module calls `SlashRedelegation` → `BeforeRedelegationSlashed(ctx, X, sharesToUnbond)`.
5. `slashRedelegationPosition` is called. `processEventsAndClaimBonus` runs:
   - Processes events, decrements their reference counts in the store.
   - Sets `pos.LastBonusAccrual = T1`, `pos.LastEventSeq = latestSeq`, `pos.LastKnownBonded = true`.
   - Computes `totalBonus > 0`.
   - Calls `sufficientBonusPoolBalance` → fails → returns `ErrInsufficientBonusPool`.
6. `slashRedelegationPosition` swallows the error. Calls `setPositionWithState(ctx, pos, nil)`.
   - Position is persisted with `LastBonusAccrual = T1`.
7. Pool is later replenished. User calls `MsgClaimTierRewards`.
8. `processEventsAndClaimBonus` starts from `LastBonusAccrual = T1`. Bonus for [T0, T1] is never computed. User receives zero bonus for that period. [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-165)
```go
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L172-230)
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

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
