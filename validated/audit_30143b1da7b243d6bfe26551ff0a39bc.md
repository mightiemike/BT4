### Title
Bonus Checkpoint Advances Without Payment When Pool Is Drained During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` advances all three bonus checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) in-memory **and** decrements event reference counts in the persistent store **before** it checks `sufficientBonusPoolBalance`. When the pool is insufficient, it returns `ErrInsufficientBonusPool`. `slashRedelegationPosition` swallows that error and then calls `setPositionWithState` with the already-advanced `pos`, permanently persisting the advanced checkpoints with no bonus payment made. The position owner loses all accrued bonus for the elapsed bonded segment with no recovery path.

---

### Finding Description

**Step 1 — Entry point.**
`BeforeRedelegationSlashed` (hooks.go:128) calls `slashRedelegationPosition` (slash.go:19).

**Step 2 — Checkpoint advancement before pool check.**
Inside `processEventsAndClaimBonus` (claim_rewards.go:142):

- The event loop (lines 172–198) calls `pos.UpdateLastEventSeq(entry.Seq)` (line 193) for every event — in-memory — and immediately calls `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` (line 196) — **written to the persistent store**.
- After the loop, `applyBonusAccrualCheckpoint(&pos.Position, blockTime)` (line 215) and `pos.UpdateLastKnownBonded(bonded)` (line 217) advance the remaining two checkpoints — in-memory.
- Only then does `k.sufficientBonusPoolBalance(ctx, bonusCoins)` (line 230) run. If the pool is drained, it returns `ErrInsufficientBonusPool` — but `pos` already carries all three advanced checkpoints. [1](#0-0) 

**Step 3 — Error swallowed, advanced position persisted.**
Back in `slashRedelegationPosition` (slash.go:54–64), the `ErrInsufficientBonusPool` branch is explicitly swallowed (only logged). Execution continues to `setPositionWithState(ctx, pos, ...)` (slash.go:71 or 77), which persists `pos` with the advanced checkpoints. [2](#0-1) 

**Step 4 — Permanent loss.**
On every future claim, `getValidatorEventsSince` starts from the now-advanced `pos.LastEventSeq`, so the elapsed segment is never re-computed. The decremented reference counts may also cause the relevant events to be garbage-collected (`decrementEventRefCount` deletes an event when `ReferenceCount <= 1`, line 95–96 of validator_events.go), making the loss irrecoverable. [3](#0-2) 

---

### Impact Explanation

A position owner permanently loses all accrued bonus rewards for the bonded segment that elapsed before the slash. The amount is proportional to `shares × tokensPerShare × BonusApy × elapsed_seconds / SecondsPerYear`. For a long-bonded, high-tier position this can be material. This is direct, irreversible economic loss to the position owner, matching the High scope target.

---

### Likelihood Explanation

The precondition — bonus pool balance below the owed amount at the exact moment `BeforeRedelegationSlashed` fires — is realistic:

1. **Natural depletion**: the pool is funded by inflation and drained by normal claims; it can reach zero or near-zero without any attacker action.
2. **Adversarial depletion**: an attacker who controls many positions can trigger mass claims in the same block to drain the pool, then cause a slash on a validator with a redelegation entry belonging to a victim position.

The hook fires automatically as part of the SDK's `SlashRedelegation` path, which is triggered by evidence submission or double-sign detection — both externally reachable production paths.

---

### Recommendation

Move all checkpoint mutations and reference-count decrements to **after** the successful coin transfer. Specifically, restructure `processEventsAndClaimBonus` so that:

1. The event loop accumulates `totalBonus` and records which events were processed, but does **not** call `pos.UpdateLastEventSeq`, `applyBonusAccrualCheckpoint`, `pos.UpdateLastKnownBonded`, or `decrementEventRefCount` until after `SendCoinsFromModuleToAccount` succeeds.
2. Only on successful payment are the checkpoints committed to `pos` and the reference counts decremented in the store.

Alternatively, in `slashRedelegationPosition`, when `ErrInsufficientBonusPool` is caught, **restore** the original checkpoint values from a snapshot taken before the call, so the position is persisted with its pre-call checkpoints and the bonus can be claimed later.

---

### Proof of Concept

```go
// Keeper test outline (unmodified Go/Cosmos test setup):
// 1. Create a position delegated to a bonded validator; let time pass so bonus accrues.
// 2. Drain RewardsPoolName module account to zero via bankKeeper.SendCoinsFromModuleToAccount.
// 3. Record pos.LastBonusAccrual and owner balance before the hook.
// 4. Call k.Hooks().BeforeRedelegationSlashed(ctx, unbondingID, sharesToUnbond).
// 5. Read the persisted position via getPositionState.
// Assert:
//   - ownerBalance == ownerBalanceBefore  (no bonus paid)
//   - pos.LastBonusAccrual > lastBonusAccrualBefore  (checkpoint advanced)
// This proves the invariant is broken: checkpoint advanced with no payment.
``` [4](#0-3) [5](#0-4) [6](#0-5)

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

**File:** x/tieredrewards/keeper/validator_events.go (L85-100)
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
