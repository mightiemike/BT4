### Title
Silent Swallow of `ErrInsufficientBonusPool` in `BeforeRedelegationSlashed` Leaves Position Bonus Checkpoints Inconsistent — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is intentionally swallowed to prevent a chain halt. However, by the time the error is returned, `processEventsAndClaimBonus` has already advanced `pos.LastEventSeq` and decremented (and potentially garbage-collected) event reference counts inside the event loop — but has **not** called `applyBonusAccrualCheckpoint` or `pos.UpdateLastKnownBonded`. The caller then persists this partially-modified position via `setPositionWithState`. The result is a position whose `LastEventSeq` is advanced past consumed events, while `LastBonusAccrual` and `LastKnownBonded` remain stale. On the next claim, the user recomputes bonus for the "forfeited" period using the wrong snapshot rate, violating the design invariant that the bonus is permanently forfeited.

---

### Finding Description

**Root cause — `processEventsAndClaimBonus` has a partial-commit ordering problem:**

Inside the event loop, two state-mutating operations happen **before** the pool balance check:

```go
// x/tieredrewards/keeper/claim_rewards.go  lines 172–198
for _, entry := range events {
    ...
    pos.UpdateLastEventSeq(entry.Seq)          // (1) advances LastEventSeq on pos
    if err := k.decrementEventRefCount(...); err != nil {  // (2) may GC the event from store
        return nil, err
    }
}
// ... compute totalBonus ...
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // ← returns here; (1) and (2) already happened
}
// These two lines are NEVER reached on pool failure:
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
pos.UpdateLastKnownBonded(bonded)
``` [1](#0-0) 

**Error is silently swallowed in `slashRedelegationPosition`:**

```go
// x/tieredrewards/keeper/slash.go  lines 54–64
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error swallowed — pos is now in inconsistent state
    } else {
        return err
    }
}
``` [2](#0-1) 

**Inconsistent position is then persisted (partial slash path):**

```go
// x/tieredrewards/keeper/slash.go  lines 66–77
fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)
if fullSlash {
    pos.Delegation = nil
    pos.ClearBonusCheckpoints()   // full slash: checkpoints reset, no further bonus
    return k.setPositionWithState(ctx, pos, ...)
}
// partial slash: ClearBonusCheckpoints is NOT called
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)  // persists inconsistent pos
``` [3](#0-2) 

After `setPositionWithState`, the persisted position has:
- `LastEventSeq` = latest consumed event seq (events consumed / GC'd from store)
- `LastBonusAccrual` = stale old value (not advanced)
- `LastKnownBonded` = stale old value (not updated)

---

### Impact Explanation

On the next `MsgClaimTierRewards` call for this position:

1. `getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)` returns only **new** events (the consumed events are gone from the store).
2. `segmentStart = pos.LastBonusAccrual` — the stale, earlier timestamp.
3. `bonded = pos.LastKnownBonded` — the stale bonded state.

The segment from the stale `LastBonusAccrual` to the first new event (or `blockTime`) is computed. This covers the **same time period** as the already-consumed events, meaning the "forfeited" bonus is effectively re-computed and paid — violating the design invariant that the bonus is permanently forfeited when the pool is empty.

Furthermore, the rate used for this re-computation is the **first new event's snapshot rate** (or the current live rate), not the snapshot rates from the consumed events. Depending on whether the rate increased or decreased, this results in:
- **Over-payment**: user receives more bonus than entitled, draining the pool faster than intended.
- **Under-payment**: user receives less bonus than entitled, causing a loss to the position owner.

The corrupted value is the `bonus` amount transferred from `types.RewardsPoolName` to the owner via `bankKeeper.SendCoinsFromModuleToAccount`. [4](#0-3) 

---

### Likelihood Explanation

The trigger requires three simultaneous conditions:
1. A tier position has an active redelegation entry (normal operation for `MsgTierRedelegate` users).
2. A partial validator slash fires on that redelegation (validator double-sign or downtime; partial slashes are the common case).
3. The bonus rewards pool (`types.RewardsPoolName`) is empty or insufficient at that block.

Condition 3 is the rarest, but the pool can be transiently empty if it has not been replenished. Conditions 1 and 2 are normal protocol events. The combination is low-probability but reachable without any privileged access — a standard delegator/validator interaction path.

---

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught and swallowed, explicitly advance the bonus checkpoints on `pos` to reflect the consumed events before calling `setPositionWithState`. Concretely, call `applyBonusAccrualCheckpoint` and `pos.UpdateLastKnownBonded` with the locally-computed `bonded` and `blockTime` values even when the pool is insufficient. This ensures the "forfeited" period is not re-computed on the next claim.

Alternatively, restructure `processEventsAndClaimBonus` so that checkpoint updates happen **before** the pool balance check, making the function safe to call even when the pool is empty.

---

### Proof of Concept

1. Owner calls `MsgTierRedelegate` for position P, creating a redelegation entry with `unbondingID = X`.
2. `setRedelegationMapping(ctx, X, P.Id)` is stored.
3. The bonus pool is drained to zero (e.g., all prior claims exhaust it).
4. A validator slash fires; `BeforeRedelegationSlashed(ctx, X, sharesToUnbond)` is called.
5. `slashRedelegationPosition` calls `processEventsAndClaimBonus(&pos)`.
6. Inside the loop, `pos.LastEventSeq` is advanced and event ref counts are decremented (events GC'd).
7. `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`; function returns early without updating `LastBonusAccrual` or `LastKnownBonded`.
8. Error is swallowed; `setPositionWithState` persists the inconsistent position (partial slash path).
9. Pool is replenished by governance or external deposit.
10. Owner calls `MsgClaimTierRewards` for position P.
11. `processEventsAndClaimBonus` finds no events (consumed in step 6), but computes a segment from the stale `LastBonusAccrual` using the current rate — paying bonus for the period that was supposed to be forfeited. [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-252)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

	totalBonus := math.ZeroInt()
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

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

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventBonusRewardsClaimed{
		PositionId: pos.Id,
		Owner:      pos.Owner,
		Rewards:    bonusCoins,
	}); err != nil {
		return nil, err
	}

	return bonusCoins, nil
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
