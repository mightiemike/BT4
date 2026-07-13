### Title
Bonus Reward Checkpoints Permanently Advanced Without Payment When Pool Is Insufficient During Redelegation Slash - (`x/tieredrewards/keeper/slash.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is deliberately swallowed to prevent chain halt. However, `processEventsAndClaimBonus` modifies the `pos` pointer in-place and writes event reference-count decrements to the store **before** checking pool balance. After the error is swallowed, `setPositionWithState` persists the advanced checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) to the store. The position owner's accrued bonus rewards are permanently unclaimable.

### Finding Description

`processEventsAndClaimBonus` takes a `*types.PositionState` pointer and mutates it in-place as it walks events:

```go
for _, entry := range events {
    // ...
    pos.UpdateLastEventSeq(entry.Seq)          // mutates *pos in-place
    if err := k.decrementEventRefCount(...);   // writes to store
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // mutates *pos
pos.UpdateLastKnownBonded(bonded)                      // mutates *pos

if totalBonus.IsZero() { return sdk.NewCoins(), nil }

if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // returns error — but *pos is already mutated and store writes committed
}
``` [1](#0-0) 

In `slashRedelegationPosition`, the `ErrInsufficientBonusPool` error is swallowed:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error swallowed — pos has advanced checkpoints, store has decremented ref counts
    } else {
        return err
    }
}
``` [2](#0-1) 

For a **partial slash**, execution continues to `setPositionWithState` which persists the mutated `pos` (with advanced `LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) to the store:

```go
pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
return k.setPositionWithState(ctx, pos, nil)   // saves pos with advanced checkpoints
``` [3](#0-2) 

`decrementEventRefCount` writes directly to the `ValidatorEvents` collection store — these writes are committed even when the error is swallowed:

```go
if event.ReferenceCount <= 1 {
    return k.ValidatorEvents.Remove(ctx, key)   // GC'd from store
}
event.ReferenceCount--
return k.ValidatorEvents.Set(ctx, key, event)   // decremented in store
``` [4](#0-3) 

After this, the position's `LastEventSeq` is advanced past the events (which may be garbage-collected), and `LastBonusAccrual` is advanced to block time. Any subsequent call to `processEventsAndClaimBonus` for this position will find no pending events and a `segmentStart` equal to the slash block time — the accrued bonus period is permanently consumed with no payment.

`ClearBonusCheckpoints` (called only on full slash) resets `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` to zero:

```go
func (p *Position) ClearBonusCheckpoints() {
    p.LastBonusAccrual = time.Time{}
    p.LastEventSeq = 0
    p.LastKnownBonded = false
}
``` [5](#0-4) 

This reset only happens on full slash. For partial slashes, the corrupted checkpoints are persisted.

### Impact Explanation

A position owner who has an active redelegation (created via `MsgTierRedelegate`) permanently loses all accrued bonus rewards for the period from `LastBonusAccrual` to the slash block time when:
1. The destination validator is slashed during the redelegation unbonding period, AND
2. The `RewardsPoolName` module account has insufficient balance to cover the accrued bonus at that moment.

The corrupted value is `pos.LastBonusAccrual` and `pos.LastEventSeq` in the `Positions` store — permanently advanced without the corresponding `SendCoinsFromModuleToAccount` transfer executing. The user cannot retry; the reward window is gone.

### Likelihood Explanation

The bonus pool is funded by governance/external mechanisms and can be empty or insufficient at any time — this is explicitly acknowledged in the ADR ("Pool empty (user-driven): Message fails atomically. No state change. Retry after pool replenished."). Validator slashing is a normal protocol event. Redelegation is a normal user action. The combination of an active redelegation + a slash + an empty pool is realistic, particularly during early chain operation or after a large bonus distribution drains the pool.

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is encountered, do **not** persist the mutated `pos` checkpoints. Either:

1. Reset the in-memory `pos` checkpoints back to their pre-call values before calling `setPositionWithState`, so the user can re-claim the bonus once the pool is replenished; or
2. Use a branched cache context for the `processEventsAndClaimBonus` call and discard it on pool-insufficient error, preserving the original position state.

The event reference-count decrements (store writes inside `decrementEventRefCount`) also need to be rolled back or avoided in this path, otherwise events may be garbage-collected before the position can process them.

### Proof of Concept

1. User calls `MsgLockTier` to create a position on validator A.
2. User calls `MsgTierRedelegate` to move the position to validator B. A `RedelegationMapping[unbondingId → positionId]` entry is created.
3. Time passes; bonus accrues on validator B.
4. The bonus pool is drained (e.g., by other claims or simply never funded).
5. Validator B is slashed while the redelegation is still in the unbonding period.
6. The staking module calls `BeforeRedelegationSlashed(ctx, unbondingId, sharesToUnbond)`.
7. `slashRedelegationPosition` calls `processEventsAndClaimBonus(&pos)`.
8. Inside `processEventsAndClaimBonus`: events are walked, `pos.LastEventSeq` is advanced, `decrementEventRefCount` writes to store, `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to block time, then `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
9. Error is swallowed in `slashRedelegationPosition`.
10. For partial slash: `setPositionWithState(ctx, pos, nil)` persists the advanced checkpoints.
11. User later calls `MsgClaimTierRewards` — `processEventsAndClaimBonus` finds no pending events (all consumed/GC'd) and `segmentStart == blockTime`, returning zero bonus.
12. The bonus accrued between the original `LastBonusAccrual` and the slash block time is permanently lost. [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
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

**File:** x/tieredrewards/keeper/validator_events.go (L85-101)
```go
func (k Keeper) decrementEventRefCount(ctx context.Context, valAddr sdk.ValAddress, seq uint64) error {
	key := collections.Join(valAddr, seq)
	event, err := k.ValidatorEvents.Get(ctx, key)
	if errors.Is(err, collections.ErrNotFound) {
		return nil // already cleaned up
	}
	if err != nil {
		return err
	}

	if event.ReferenceCount <= 1 {
		return k.ValidatorEvents.Remove(ctx, key)
	}

	event.ReferenceCount--
	return k.ValidatorEvents.Set(ctx, key, event)
}
```

**File:** x/tieredrewards/types/position.go (L80-84)
```go
func (p *Position) ClearBonusCheckpoints() {
	p.LastBonusAccrual = time.Time{}
	p.LastEventSeq = 0
	p.LastKnownBonded = false
}
```
