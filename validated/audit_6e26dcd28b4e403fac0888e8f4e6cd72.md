### Title
Silently Swallowed Bonus Transfer Failure Permanently Destroys Accrued Rewards During Redelegation Slash - (File: x/tieredrewards/keeper/slash.go)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is deliberately swallowed. However, `processEventsAndClaimBonus` takes `pos` by pointer and advances the position's bonus accrual checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) in memory **before** the pool-sufficiency check. The caller then persists the position with those advanced checkpoints via `setPositionWithState`. The result is that the accrued bonus period is permanently erased from the position's state without the owner ever receiving the coins.

### Finding Description

`processEventsAndClaimBonus` modifies the `pos *types.PositionState` pointer in-place throughout its execution — advancing `LastEventSeq` inside the event loop, then calling `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` — before it reaches the pool-sufficiency guard: [1](#0-0) 

When `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`, the function returns `nil, err` with `pos` already mutated. Back in `slashRedelegationPosition`, the error branch for `ErrInsufficientBonusPool` logs and falls through — it does **not** restore the position's checkpoints: [2](#0-1) 

Execution then reaches `setPositionWithState`, which persists the mutated `pos` (with advanced checkpoints but zero bonus paid) to the store: [3](#0-2) 

On the next claim, `processEventsAndClaimBonus` will start its event replay from the already-advanced `LastEventSeq` and `LastBonusAccrual`, so the bonus that accrued up to the slash event is gone forever.

### Impact Explanation

A position owner permanently loses all bonus rewards that accrued between their last claim and the moment of the redelegation slash. The `RewardsPoolName` module account balance is not reduced (the transfer never happened), but the position's accrual window is irreversibly advanced past the owed period. There is no recovery path: the owner cannot re-claim the lost interval because the checkpoints have moved forward. [4](#0-3) 

### Likelihood Explanation

The condition requires two simultaneous facts: (a) a validator with at least one tier position has an active redelegation that is slashed, and (b) the `RewardsPoolName` balance is below the computed bonus at that instant. Condition (b) is explicitly anticipated by the ADR ("Bonus forfeits silently if the pool is insufficient — chain-halt avoidance") and by the comment at line 55 of `slash.go`. The pool can be legitimately low during high-claim periods or near depletion. A double-sign slash on a popular validator during a low-pool window is a realistic, non-privileged trigger. No special attacker capability is required beyond holding a tier position on the affected validator. [5](#0-4) 

### Recommendation

Decouple checkpoint advancement from the transfer. Before calling `sufficientBonusPoolBalance`, snapshot the checkpoint fields of `pos`. If the pool check fails, restore the snapshot so the position is persisted with its original checkpoints. This ensures the owner can retry the claim once the pool is replenished, matching the documented behavior for all other user-driven paths (ClaimTierRewards, Redelegate, Undelegate, ClearPosition).

Alternatively, advance checkpoints only after a successful `SendCoinsFromModuleToAccount`, so a failed transfer leaves the position's accrual window intact. [6](#0-5) 

### Proof of Concept

1. Alice holds a tier position on validator V with 1 year of unclaimed bonus accrued.
2. Alice redelegates the position to validator V2 via `MsgTierRedelegate`. A `RedelegationMapping` entry is created.
3. The `RewardsPoolName` balance drops below Alice's accrued bonus (e.g., due to many concurrent claims).
4. Validator V is slashed for double-signing. The staking module fires `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
5. `processEventsAndClaimBonus` walks all events, advances `pos.LastEventSeq`, `pos.LastBonusAccrual`, and `pos.LastKnownBonded` to `blockTime`, then returns `ErrInsufficientBonusPool`.
6. `slashRedelegationPosition` swallows the error and calls `setPositionWithState` with the mutated `pos`.
7. Alice later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the already-advanced checkpoints and computes zero bonus for the lost interval. Alice receives nothing for the 1-year accrual period. [7](#0-6) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-220)
```go
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
