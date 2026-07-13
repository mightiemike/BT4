### Title
Bonus Accrual Checkpoints Advanced Without Payment When Pool Is Insufficient in `slashRedelegationPosition` — (`x/tieredrewards/keeper/slash.go`)

### Summary

In `x/tieredrewards/keeper/slash.go`, `slashRedelegationPosition` silently swallows `ErrInsufficientBonusPool` from `processEventsAndClaimBonus` to avoid chain halts. However, `processEventsAndClaimBonus` mutates the position's bonus-accrual checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) **in memory before** the pool-sufficiency check, and also decrements event reference counts **in the store** before that check. When the pool check fails and the error is swallowed, `setPositionWithState` is called with the already-advanced checkpoints, persisting them without any bonus payment. The accrual period is permanently consumed; the user can never recover the forfeited bonus even after the pool is replenished.

### Finding Description

**Root cause — `processEventsAndClaimBonus` mutates state before the pool check:** [1](#0-0) 

Inside the event loop, `pos.UpdateLastEventSeq(entry.Seq)` is called and `k.decrementEventRefCount` writes to the store — both happen before the pool-sufficiency check. After the loop: [2](#0-1) 

`applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` advance the in-memory position checkpoints, and only then is `sufficientBonusPoolBalance` checked. When the check fails, the function returns an error with `pos` already mutated.

**Root cause — `slashRedelegationPosition` swallows the error and persists the mutated state:** [3](#0-2) 

The `ErrInsufficientBonusPool` branch logs the error and falls through. The code then calls `setPositionWithState` with the `pos` whose checkpoints were already advanced by `processEventsAndClaimBonus`. The advanced `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` are written to the store, permanently consuming the accrual window without payment.

**Entry path:**

1. Any unprivileged user calls `MsgTierRedelegate` — a normal user-facing transaction.
2. The destination validator is slashed during the redelegation period.
3. The staking module fires `BeforeRedelegationSlashed` → `slashRedelegationPosition`.
4. The bonus pool is insufficient at that moment (a normal operational state: the pool drains via BeginBlocker top-ups and user bonus claims).
5. `processEventsAndClaimBonus` advances checkpoints and decrements event reference counts, then returns `ErrInsufficientBonusPool`.
6. The error is swallowed; `setPositionWithState` persists the advanced checkpoints. [4](#0-3) 

### Impact Explanation

**Impact: High.**

The position owner permanently loses all bonus rewards accrued from `pos.LastBonusAccrual` up to the slash block time. Because the checkpoints are persisted, the next `ClaimTierRewards` call starts from the slash block time — the lost window is never replayed. This is a direct, irrecoverable loss of user funds (bonus rewards denominated in the bond denom). The corrupted values are `pos.LastBonusAccrual`, `pos.LastEventSeq`, and `pos.LastKnownBonded` on the affected `Position` record, plus the decremented `ValidatorEvents` reference counts in the store. [5](#0-4) 

### Likelihood Explanation

**Likelihood: Medium.**

Three conditions must coincide: (1) a user has a redelegating tier position, (2) the destination validator is slashed during the redelegation window, and (3) the bonus pool is insufficient at that moment. Condition (1) is a normal use case. Condition (2) is a normal protocol event. Condition (3) is a normal operational state — the pool is drained by the BeginBlocker base-rewards top-up every block and by user bonus claims; it requires active external funding to remain solvent. [6](#0-5) 

The ADR documents "Bonus forfeits silently if the pool is insufficient (chain-halt avoidance)" but does not document that the checkpoints are permanently advanced without payment, making recovery impossible. [7](#0-6) 

### Recommendation

In `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is encountered, the position's bonus checkpoints must **not** be advanced. The fix requires restructuring `processEventsAndClaimBonus` so that the pool-sufficiency check occurs **before** any state mutation (checkpoint advancement and event reference-count decrements). One approach:

1. Compute `totalBonus` and collect the events to process in a read-only pass.
2. Check `sufficientBonusPoolBalance` before modifying `pos` or calling `decrementEventRefCount`.
3. Only if the pool check passes, advance checkpoints, decrement reference counts, and send coins.

Alternatively, run `processEventsAndClaimBonus` inside a `CacheContext` in `slashRedelegationPosition` and only commit the cache if the call succeeds; discard it (including the reference-count decrements) on `ErrInsufficientBonusPool`.

### Proof of Concept

```
1. User calls MsgTierRedelegate(position_id=P, dst_validator=V2).
   → RedelegationMappings[unbondingId] = P is stored.

2. Bonus pool drains to zero (BeginBlocker top-ups or other users claiming).

3. V2 is slashed. Staking fires BeforeRedelegationSlashed(unbondingId, sharesToUnbond).
   → slashRedelegationPosition(ctx, unbondingId, sharesToUnbond)
   → pos = getPositionState(P)  // pos.LastBonusAccrual = T0
   → processEventsAndClaimBonus(ctx, &pos)
       - walks events, computes totalBonus > 0
       - decrementEventRefCount (store write, persistent)
       - applyBonusAccrualCheckpoint: pos.LastBonusAccrual = blockTime (T1)
       - pos.LastEventSeq advanced
       - sufficientBonusPoolBalance → ErrInsufficientBonusPool → return error
   → error is ErrInsufficientBonusPool → swallowed
   → setPositionWithState(ctx, pos, ...)
       persists pos.LastBonusAccrual = T1, pos.LastEventSeq = advanced

4. Pool is replenished. User calls MsgClaimTierRewards(position_id=P).
   → processEventsAndClaimBonus starts from pos.LastBonusAccrual = T1
   → The period [T0, T1] is permanently lost; user receives zero bonus for that window.
``` [3](#0-2) [8](#0-7)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
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

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/abci.go (L113-116)
```go
	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
	if err != nil {
		return err
	}
```

**File:** doc/architecture/adr-006.md (L310-310)
```markdown
| **BeforeRedelegationSlashed** | Fires before staking's `Unbond` in `SlashRedelegation`. Routes via `RedelegationMappings[unbondingId]` to the affected position and runs `processEventsAndClaimBonus` against **pre-slash** shares. Base rewards auto-withdraw inside distribution's `BeforeDelegationSharesModified` (still fired by the subsequent `Unbond`). On full slash, `pos.Delegation` is set to nil and checkpoints reset. Bonus forfeits silently if the pool is insufficient (chain-halt avoidance). |
```
