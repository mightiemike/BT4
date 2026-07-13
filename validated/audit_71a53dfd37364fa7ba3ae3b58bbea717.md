Let me look at the exact checkpoint update ordering and the `setPositionWithState` call to confirm the atomicity issue.

### Title
Non-Atomic Bonus Checkpoint Advancement on `ErrInsufficientBonusPool` in `slashRedelegationPosition` — (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` advances all in-memory bonus checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and decrements persistent event reference counts **before** it checks whether the bonus pool has sufficient balance. When the pool check fails with `ErrInsufficientBonusPool`, the function returns the error without paying. `slashRedelegationPosition` deliberately swallows that error and then calls `setPositionWithState` with the already-mutated `pos`, permanently persisting the advanced checkpoints. The accrued bonus for that period is irrecoverably lost.

---

### Finding Description

**Step 1 — Entry point.**
A validator slash triggers `BeforeRedelegationSlashed` for every live redelegation entry on that validator. Each call routes to `slashRedelegationPosition`. [1](#0-0) 

**Step 2 — Checkpoint mutation happens before the pool check.**
Inside `processEventsAndClaimBonus`, the event loop advances `pos.LastEventSeq` and calls `decrementEventRefCount` (a persistent store write) for every event: [2](#0-1) 

After the loop, `applyBonusAccrualCheckpoint` and `UpdateLastKnownBonded` mutate the in-memory `pos`: [3](#0-2) 

Only **after** all of the above does the code check pool sufficiency: [4](#0-3) 

If the pool is insufficient, the function returns `ErrInsufficientBonusPool` — no payment is made, but `pos` already carries the advanced checkpoints.

**Step 3 — Error is swallowed; mutated position is persisted.**
`slashRedelegationPosition` catches `ErrInsufficientBonusPool` and logs it, then falls through to `setPositionWithState` with the already-mutated `pos`: [5](#0-4) 

`setPositionWithState` writes the position (with advanced checkpoints) to the store: [6](#0-5) 

The bonus accrual window is now permanently closed with no payment ever sent.

**Step 4 — Multi-position amplification.**
When a delegator holds N positions each with a live redelegation entry on the same validator, the SDK fires `BeforeRedelegationSlashed` once per `unbondingID`. The calls are sequential within the same block. If the pool holds exactly enough for one payment:

- Position 1: `sufficientBonusPoolBalance` passes → `SendCoinsFromModuleToAccount` drains the pool.
- Position 2…N: `sufficientBonusPoolBalance` fails → error swallowed → checkpoints advanced → bonus permanently lost.

`sufficientBonusPoolBalance` reads the live bank balance each time, so the depletion from position 1 is visible to position 2: [7](#0-6) 

---

### Impact Explanation

Permanent, unrecoverable loss of accrued bonus rewards for every position whose `slashRedelegationPosition` call encounters an insufficient pool. The checkpoints are advanced, so the skipped period can never be reclaimed. This is a direct accounting flaw: the module advances its internal "paid-through" marker without transferring the corresponding value, violating the invariant that checkpoint advancement must be atomic with payment.

---

### Likelihood Explanation

The pool can be legitimately depleted by normal ABCI end-block reward distribution or by a burst of concurrent slash events. A delegator with N positions on a validator that gets slashed while the pool is low (a realistic operational condition) will reliably trigger this path. No governance, operator, or privileged access is required — only a standard validator slash event.

---

### Recommendation

Move `applyBonusAccrualCheckpoint`, `UpdateLastKnownBonded`, `UpdateLastEventSeq`, and `decrementEventRefCount` to execute **only after** `sufficientBonusPoolBalance` and `SendCoinsFromModuleToAccount` both succeed. Alternatively, when `ErrInsufficientBonusPool` is detected in `slashRedelegationPosition`, do **not** call `setPositionWithState` with the mutated `pos`; instead restore the original position or skip the persist entirely so the checkpoint is not advanced.

---

### Proof of Concept

```
1. Create validator V with two tier positions P1 and P2, each with a live
   redelegation entry (unbondingID1, unbondingID2).
2. Fund the bonus pool with exactly enough for one bonus payment.
3. Trigger a validator slash on V.
4. SDK fires BeforeRedelegationSlashed(unbondingID1) → payment for P1 succeeds,
   pool is now empty.
5. SDK fires BeforeRedelegationSlashed(unbondingID2) → sufficientBonusPoolBalance
   fails → ErrInsufficientBonusPool swallowed → P2's LastBonusAccrual and
   LastEventSeq are advanced and persisted.
6. Assert: P2.LastBonusAccrual == blockTime (checkpoint advanced).
7. Assert: P2 owner received 0 bonus coins.
8. Assert: no future claim can recover the skipped period (checkpoint already past it).
```

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-197)
```go
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-217)
```go
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

**File:** x/tieredrewards/keeper/position.go (L126-141)
```go
func (k Keeper) setPositionWithState(ctx context.Context, state types.PositionState, update *ValidatorTransition) error {
	if err := state.Validate(); err != nil {
		return err
	}

	pos := state.Position

	oldPos, err := k.getPosition(ctx, pos.Id)
	isNew := errors.Is(err, types.ErrPositionNotFound)
	if !isNew && err != nil {
		return err
	}

	if err := k.Positions.Set(ctx, pos.Id, pos); err != nil {
		return err
	}
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
