### Title
Silent Bonus Checkpoint Advancement on Insufficient Pool During Redelegation Slash Causes Permanent Loss of Accrued Bonus Rewards - (`x/tieredrewards/keeper/slash.go`)

---

### Summary

In `x/tieredrewards/keeper/slash.go`, the `slashRedelegationPosition` function calls `processEventsAndClaimBonus` and silently swallows `ErrInsufficientBonusPool`. Because `processEventsAndClaimBonus` advances the position's bonus checkpoints (`LastEventSeq`, `LastBonusAccrual`, `LastKnownBonded`) and decrements event reference counts in the KV store **before** performing the pool sufficiency check, a pool-insufficient failure leaves the position with permanently advanced checkpoints but zero bonus paid. The user can never recover the forfeited bonus.

---

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` performs two categories of mutations before the pool check:

**1. In-memory mutations to `pos` (lines 193, 215, 217):**
```go
pos.UpdateLastEventSeq(entry.Seq)          // line 193 — advances event cursor
...
applyBonusAccrualCheckpoint(&pos.Position, blockTime)  // line 215 — advances time cursor
pos.UpdateLastKnownBonded(bonded)          // line 217 — advances bonded state
```

**2. Persistent KV store writes (line 196):**
```go
if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {  // line 196
```
`decrementEventRefCount` writes directly to `k.ValidatorEvents` in the KV store. When the reference count reaches zero, the event is deleted from the store entirely.

Only **after** all of the above does the pool check occur:
```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err   // line 230-232 — returns error with pos already mutated
}
```

In `slashRedelegationPosition`, the returned `ErrInsufficientBonusPool` is explicitly swallowed:
```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // falls through — pos has advanced checkpoints, store has decremented ref counts
    } else {
        return err
    }
}
// ...
return k.setPositionWithState(ctx, pos, ...)  // persists advanced checkpoints
```

`setPositionWithState` then persists the `pos` with its advanced checkpoints. The result:
- `LastEventSeq` is advanced past all processed events → future `processEventsAndClaimBonus` calls start after these events, skipping the unpaid window
- `LastBonusAccrual` is set to the current block time → the time window is consumed
- Event reference counts are decremented in the store → events may be garbage-collected, destroying the data needed to recompute the bonus

This is structurally identical to the reported vulnerability: a pre-accounting step (checkpoint/cursor advancement) is performed before the actual resource availability check, and a silent failure path leaves the accounting advanced but the payout undelivered.

---

### Impact Explanation

A tier position owner whose validator is slashed during an active redelegation, when the `RewardsPoolName` module account has insufficient balance, permanently loses all accrued bonus rewards for the period up to the slash block. The loss is irreversible because:
1. The position's event cursor (`LastEventSeq`) is advanced past the events that were processed
2. The event reference counts are decremented in the KV store (events may be deleted)
3. The time cursor (`LastBonusAccrual`) is set to the current block time

The corrupted value is: `pos.LastBonusAccrual`, `pos.LastEventSeq`, `pos.LastKnownBonded` — all advanced without the corresponding `bonusCoins` transfer from `RewardsPoolName` to the owner.

---

### Likelihood Explanation

The trigger requires two concurrent conditions:
1. A tier position with an active redelegation entry (created via `MsgTierRedelegate`)
2. The destination validator is slashed (double-sign or downtime) while the `RewardsPoolName` module account balance is less than the accrued bonus

The bonus pool can be empty or insufficient in realistic scenarios:
- Early chain operation before the pool is funded
- The pool is drained by the `topUpBaseRewards` BeginBlocker (which drains the same `RewardsPoolName` every block when there is a shortfall)
- Many concurrent bonus claims have depleted the pool

Validator slashing is a normal, unprivileged staking event triggered by consensus misbehavior or downtime. No attacker control is required — the condition arises from ordinary chain operation.

---

### Recommendation

Move the pool sufficiency check and the `SendCoinsFromModuleToAccount` call **before** advancing any checkpoints or decrementing event reference counts. Alternatively, perform all checkpoint mutations only after a successful transfer, or use a two-phase approach: compute the bonus amount first (read-only pass), check pool sufficiency, then advance checkpoints and transfer atomically.

For the `slashRedelegationPosition` path specifically, if the pool is insufficient, the function should either:
- Defer the bonus claim (store a pending claim record) so the user can retry, or
- Not advance the checkpoints, so the user can claim later when the pool is replenished

---

### Proof of Concept

1. User calls `MsgTierRedelegate` to redelegate a tier position from validator A to validator B. This creates a redelegation mapping entry.

2. The `RewardsPoolName` module account is empty (e.g., drained by `topUpBaseRewards` BeginBlocker).

3. Validator B is slashed (double-sign). The staking module fires `BeforeRedelegationSlashed`.

4. `slashRedelegationPosition` is called:
   - `processEventsAndClaimBonus(ctx, &pos)` is called
   - Inside, the loop at line 172 iterates events, calling `pos.UpdateLastEventSeq(entry.Seq)` (line 193) and `k.decrementEventRefCount(ctx, valAddr, entry.Seq)` (line 196) for each event
   - `applyBonusAccrualCheckpoint` (line 215) sets `pos.LastBonusAccrual = blockTime`
   - `sufficientBonusPoolBalance` (line 230) returns `ErrInsufficientBonusPool`
   - The error is returned to `slashRedelegationPosition`

5. `slashRedelegationPosition` swallows the error (lines 56-63) and calls `setPositionWithState(ctx, pos, ...)` with the mutated `pos`.

6. The position is now persisted with `LastEventSeq` advanced and `LastBonusAccrual = blockTime`. The event reference counts in the KV store are decremented (events may be deleted).

7. User later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the advanced `LastEventSeq` and `LastBonusAccrual`, finding no pending events and zero accrual time. Zero bonus is returned. The user's accrued bonus is permanently lost.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/slash.go (L53-64)
```go
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
```

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

**File:** x/tieredrewards/keeper/validator_events.go (L83-101)
```go
// decrementEventRefCount decrements the reference count of a validator event.
// If the reference count reaches zero, the event is deleted.
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
