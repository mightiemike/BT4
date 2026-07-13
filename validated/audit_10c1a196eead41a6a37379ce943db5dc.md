### Title
Bonus Rewards Permanently Forfeited When Pool Is Insufficient During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`, the error is silently swallowed. However, by the time that error is returned, the position's bonus accrual checkpoints have already been advanced in memory **and** validator event reference counts have already been decremented in the store. The position is then persisted with the advanced checkpoints but with zero bonus paid. The accrued bonus is permanently unrecoverable.

---

### Finding Description

`processEventsAndClaimBonus` performs two categories of state mutation before it reaches the pool-balance guard:

**1. In-memory position mutations (via `&pos` pointer):**

- `pos.UpdateLastEventSeq(entry.Seq)` — inside the event loop
- `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` — advances `LastBonusAccrual`
- `pos.UpdateLastKnownBonded(bonded)` — updates bonded state [1](#0-0) 

**2. Persistent store mutations:**

- `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` — decrements the reference count for each processed event inside the loop, potentially garbage-collecting events [2](#0-1) 

Only **after** all of the above does the function check pool sufficiency: [3](#0-2) 

When the pool check fails, `ErrInsufficientBonusPool` is returned. Back in `slashRedelegationPosition`, this error is explicitly swallowed:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
    } else {
        return err
    }
}
``` [4](#0-3) 

Execution then falls through to `setPositionWithState`, which persists the `pos` object — now carrying the advanced checkpoints — to the store: [5](#0-4) 

The net result:

| State | Outcome |
|---|---|
| `pos.LastEventSeq` | Advanced past all processed events |
| `pos.LastBonusAccrual` | Advanced to current block time |
| Event reference counts | Decremented (events may be GC'd) |
| Bonus coins transferred | **Zero** |

The position owner can never reclaim the forfeited bonus: the checkpoints have moved past the accrual window, and the events that encoded the accrual period may have been garbage-collected.

This is the direct analog of M-06: a calculated payment (bonus) is skipped entirely when the available balance (pool) is less than the owed amount, instead of paying the available remainder.

---

### Impact Explanation

Position owners with active redelegations permanently lose all accrued bonus rewards whenever a validator is slashed while the `RewardsPoolName` module account holds less than the computed bonus. The funds remain locked in the pool and are never routed to the intended recipient. The broken invariant is: *event reference counts are decremented and checkpoints are advanced without the corresponding bonus being paid*, making the loss irrecoverable.

---

### Likelihood Explanation

The bonus pool is a finite module account funded by governance. As positions accumulate and users claim rewards over time, the pool balance naturally decreases. Validator slashes are routine protocol events (double-sign evidence, downtime). The intersection of a depleted pool and a slash affecting a position with an active redelegation is a realistic production scenario, not a theoretical edge case.

---

### Recommendation

Mirror the M-06 fix: instead of forfeiting the entire bonus when the pool is insufficient, pay whatever partial amount is available. In `slashRedelegationPosition`, after catching `ErrInsufficientBonusPool`, compute the available pool balance and transfer it to the owner before saving the position. Alternatively, restructure `processEventsAndClaimBonus` so that all checkpoint and reference-count mutations occur **after** the pool balance is confirmed sufficient, preserving the ability to retry on the next claim.

---

### Proof of Concept

1. A position owner creates a tier position and redelegates to validator `V`.
2. The `RewardsPoolName` balance is less than the position's accrued bonus (pool naturally depleted by prior claims).
3. Validator `V` is slashed (e.g., double-sign evidence submitted on-chain — a normal `MsgSubmitEvidence` transaction).
4. The staking module fires `BeforeRedelegationSlashed` → `slashRedelegationPosition` is called.
5. `processEventsAndClaimBonus` walks all pending events, decrementing their reference counts in the store and advancing `pos.LastEventSeq`, `pos.LastBonusAccrual`, `pos.LastKnownBonded` in memory.
6. The pool balance check at line 230 fails → `ErrInsufficientBonusPool` returned.
7. `slashRedelegationPosition` swallows the error (lines 56–63).
8. `setPositionWithState` persists `pos` with the advanced checkpoints.
9. The position owner's earned bonus is permanently lost; the pool retains the funds. [4](#0-3) [1](#0-0) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-217)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L54-64)
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
```

**File:** x/tieredrewards/keeper/slash.go (L66-77)
```go
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

**File:** x/tieredrewards/keeper/bonus_rewards.go (L48-61)
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
	if bonus.IsZero() {
		return nil
	}

	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
	if !poolBalance.IsAllGTE(bonus) {
		return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
			"bonus: %s, pool balance: %s",
			bonus.String(), poolBalance.String())
	}

	return nil
```
