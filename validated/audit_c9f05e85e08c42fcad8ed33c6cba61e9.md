### Title
Bonus Reward Checkpoints Advance Without Payment on Insufficient Pool During Redelegation Slash — (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the position's reward checkpoints (`LastBonusAccrual`, `LastEventSeq`, `LastKnownBonded`) have already been permanently advanced and validator event reference counts already decremented in the KV store. The error is silently swallowed and the position is then saved with those advanced checkpoints via `setPositionWithState`. The owner's earned bonus rewards are permanently destroyed — they can never be recovered even after the pool is replenished.

---

### Finding Description

`processEventsAndClaimBonus` performs two categories of state mutation before it checks whether the pool can cover the computed bonus:

**Category 1 — persistent KV-store writes (inside the event loop):** [1](#0-0) 

`pos.UpdateLastEventSeq(entry.Seq)` advances the in-memory sequence pointer, and `k.decrementEventRefCount` writes to the KV store immediately — events whose reference count reaches zero are garbage-collected at this point, before any payment is attempted.

**Category 2 — in-memory checkpoint advancement (after the loop):** [2](#0-1) 

`applyBonusAccrualCheckpoint` sets `LastBonusAccrual = blockTime` and `UpdateLastKnownBonded` records the final bonded state — both on the in-memory `pos` pointer — before the pool sufficiency check at line 230.

**The pool check that fails:** [3](#0-2) 

When this returns `ErrInsufficientBonusPool`, all the mutations above have already occurred. No bonus is transferred.

**The error is swallowed in the slash hook:** [4](#0-3) 

Execution continues past the error. The position — now carrying advanced checkpoints — is then persisted: [5](#0-4) 

Both the full-slash and partial-slash branches call `setPositionWithState` with the mutated `pos`, permanently committing the advanced checkpoints to state.

**Contrast with user-driven paths**, which fail atomically and allow retry: [6](#0-5) 

---

### Impact Explanation

A position owner permanently loses all earned bonus rewards accrued up to the slash block when:
1. Their tier position is in an active redelegation (created via `MsgTierRedelegate`), and
2. The source validator is slashed while the bonus rewards pool is insufficient to cover the owed bonus.

After the slash, the position's `LastBonusAccrual` is set to the slash block time and `LastEventSeq` is advanced past all processed events. Even after the pool is replenished, the owner cannot claim the bonus for the destroyed period — the accounting window has been permanently closed. The funds remain locked in the rewards pool module account but are inaccessible to the rightful owner.

---

### Likelihood Explanation

The rewards pool is a finite module account funded by governance or inflation top-ups. A large validator slash, a pool that has not been recently replenished, or a period of high claim activity can all produce an insufficient-pool condition. Redelegations are a normal user action (`MsgTierRedelegate`), and validator slashing is a normal protocol event triggered by evidence submission — no privileged access is required. The combination is realistic in production.

---

### Recommendation

Move `applyBonusAccrualCheckpoint`, `UpdateLastKnownBonded`, and `pos.UpdateLastEventSeq` / `decrementEventRefCount` to execute **only after** a successful `SendCoinsFromModuleToAccount`. If the pool is insufficient in the slash hook, either:

- Skip advancing checkpoints entirely (leave the position at its pre-call state so the owner can claim later when the pool is replenished), or
- Advance checkpoints only after a successful payment, and do not decrement event reference counts until payment succeeds.

The current design atomically fails on user-driven paths (correct) but silently corrupts state on the slash-hook path (incorrect).

---

### Proof of Concept

1. User A creates a tier position and calls `MsgTierRedelegate` to move delegation from validator V1 to V2. A `RedelegationMapping` entry is created.
2. The bonus rewards pool is drained (e.g., by other claimants or simply not replenished).
3. V1 is slashed. The staking module calls `SlashRedelegation`, which fires `BeforeRedelegationSlashed`.
4. `slashRedelegationPosition` is invoked. `processEventsAndClaimBonus` runs, advances `pos.LastBonusAccrual` to `blockTime`, advances `pos.LastEventSeq`, decrements event ref counts (potentially GC-ing events), then returns `ErrInsufficientBonusPool`.
5. The error is logged and swallowed. `setPositionWithState` persists the position with the advanced checkpoints.
6. The pool is later replenished. User A calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the now-advanced `LastBonusAccrual` and `LastEventSeq` — the pre-slash accrual period is gone. User A receives zero bonus for the destroyed period.

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L193-198)
```go
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
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

**File:** x/tieredrewards/keeper/slash.go (L68-77)
```go
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

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```
