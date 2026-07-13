### Title
Incorrect `LastKnownBonded = true` Hardcode in `createDelegatedPosition` Allows Bonus Accrual During Unbonded Validator Period — (`x/tieredrewards/keeper/position.go`, `claim_rewards.go`)

---

### Summary

When a position is created after a validator's UNBOND event has already been appended (seq=S), `createDelegatedPosition` sets `LastEventSeq = S` and hardcodes `LastKnownBonded = true`. Because `getValidatorEventsSince` uses `StartExclusive(S)`, the UNBOND event at seq=S is never replayed for this position. When the validator later re-bonds (BOND event at seq=S+1), `processEventsAndClaimBonus` sees `bonded = true` (the incorrect default) and computes bonus for the entire segment `[T_create, T_rebond]` — a period during which the validator was fully unbonded. The position owner receives bonus rewards they are not entitled to, draining the `RewardsPoolName` module account.

---

### Finding Description

**Step 1 — UNBOND event is appended (seq=S):**

`AfterValidatorBeginUnbonding` fires and calls `appendValidatorEvent`, storing an UNBOND event at seq=S. [1](#0-0) 

**Step 2 — Position is created after the UNBOND event:**

`createDelegatedPosition` reads the latest event seq via `getValidatorEventLatestSeq`, which returns S (the UNBOND event). It then constructs the position with `lastEventSeq = S` and **hardcodes `lastKnownBonded = true`**, regardless of the validator's actual current bonded state: [2](#0-1) 

The `NewPosition` call passes `true` as the `lastKnownBonded` argument unconditionally: [3](#0-2) 

**Step 3 — Validator re-bonds (BOND event at seq=S+1):**

`AfterValidatorBonded` appends a BOND event at seq=S+1. [4](#0-3) 

**Step 4 — Claim triggers `processEventsAndClaimBonus`:**

`getValidatorEventsSince` is called with `startSeq = S`. Because the range is `StartExclusive(S)`, it returns only events with seq > S — i.e., only the BOND event at seq=S+1. The UNBOND event at seq=S is **never seen** by this position. [5](#0-4) 

**Step 5 — Bonus is incorrectly computed for the unbonded gap:**

`processEventsAndClaimBonus` initializes `bonded = pos.LastKnownBonded = true`. When it processes the BOND event at seq=S+1 (timestamp=T_rebond), the `if bonded` branch fires and computes bonus for `[T_create, T_rebond]` using the BOND event's `TokensPerShare`. This covers the entire period during which the validator was unbonded: [6](#0-5) 

After the loop, `bonded = true` and `val.IsBonded() = true`, so the tail segment `[T_rebond, blockTime]` is also computed. The position owner receives bonus for both segments, including the unbonded gap.

---

### Impact Explanation

The position owner receives bonus rewards from `RewardsPoolName` for a period during which the validator was unbonded. This is a direct, measurable fund loss from the module's reward pool. The exact delta is:

```
bonus = shares * tokensPerShare * tier.BonusApy * (T_rebond - T_create) / SecondsPerYear
```

This can be non-trivial for large positions or long unbonding periods (Cosmos SDK default unbonding is 21 days).

---

### Likelihood Explanation

- Validator unbonding and re-bonding are normal, permissionless chain events.
- Any user can create a position in the window between an UNBOND event and a BOND event.
- No special privileges, governance, or operator compromise is required.
- The attack is repeatable across multiple validators and multiple positions.

The same incorrect hardcode `true` also appears in `TierRedelegate` when updating bonus checkpoints for the destination validator: [7](#0-6) 

This creates an identical vulnerability for redelegation to an unbonded destination validator.

---

### Recommendation

In `createDelegatedPosition`, replace the hardcoded `true` with a live check of the validator's current bonded state:

```go
val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
if err != nil {
    return types.Position{}, err
}
lastKnownBonded := val.IsBonded()
pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, lastKnownBonded, blockTime)
```

Apply the same fix in `TierRedelegate` when calling `pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)` — replace `true` with `dstVal.IsBonded()`.

---

### Proof of Concept

```
1. Validator V is bonded. No positions exist for V.
2. V begins unbonding → AfterValidatorBeginUnbonding fires → UNBOND event seq=1 stored.
3. Attacker calls MsgLockTier targeting V.
   → createDelegatedPosition: lastEventSeq=1, LastKnownBonded=true (BUG).
   → Position P created at T_create.
4. V re-bonds → AfterValidatorBonded fires → BOND event seq=2 stored at T_rebond.
5. Attacker calls MsgClaimTierRewards for P.
   → getValidatorEventsSince(valAddr, 1) → returns only BOND event (seq=2).
   → bonded = pos.LastKnownBonded = true (incorrect).
   → Loop: bonded=true → computeSegmentBonus([T_create, T_rebond]) → non-zero bonus paid.
   → Tail: bonded=true, val.IsBonded()=true → computeSegmentBonus([T_rebond, now]) → bonus paid.
6. Attacker receives bonus for [T_create, T_rebond] — the entire unbonded period.
   Expected: zero bonus for that gap. Actual: non-zero bonus paid from RewardsPoolName.
```

### Citations

**File:** x/tieredrewards/keeper/hooks.go (L27-49)
```go
func (h Hooks) AfterValidatorBeginUnbonding(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
```

**File:** x/tieredrewards/keeper/hooks.go (L53-76)
```go
func (h Hooks) AfterValidatorBonded(ctx context.Context, _ sdk.ConsAddress, valAddr sdk.ValAddress) error {
	count, err := h.k.getPositionCountForValidator(ctx, valAddr)
	if err != nil {
		return err
	}
	if count == 0 {
		return nil
	}

	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
}
```

**File:** x/tieredrewards/keeper/position.go (L47-56)
```go
	lastEventSeq, err := k.getValidatorEventLatestSeq(ctx, valAddr)
	if err != nil {
		return types.Position{}, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()
	blockHeight := uint64(sdkCtx.BlockHeight())

	pos := types.NewPosition(id, owner, tier.Id, delAddr.String(), blockHeight, lastEventSeq, blockTime, true, blockTime)
```

**File:** x/tieredrewards/types/position.go (L15-27)
```go
func NewPosition(id uint64, owner string, tierId uint32, delegatorAddress string, createdAtHeight, lastEventSeq uint64, lastBonusAccrual time.Time, lastKnownBonded bool, createdAtTime time.Time) Position {
	return Position{
		Id:               id,
		Owner:            owner,
		TierId:           tierId,
		DelegatorAddress: delegatorAddress,
		CreatedAtHeight:  createdAtHeight,
		CreatedAtTime:    createdAtTime,
		LastEventSeq:     lastEventSeq,
		LastBonusAccrual: lastBonusAccrual,
		LastKnownBonded:  lastKnownBonded,
	}
}
```

**File:** x/tieredrewards/keeper/validator_events.go (L67-81)
```go
func (k Keeper) getValidatorEventsSince(ctx context.Context, valAddr sdk.ValAddress, startSeq uint64) ([]EventEntry, error) {
	// Range from (valAddr, startSeq+1) to end of valAddr prefix.
	rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr).
		StartExclusive(startSeq)

	var entries []EventEntry
	err := k.ValidatorEvents.Walk(ctx, rng, func(key collections.Pair[sdk.ValAddress, uint64], event types.ValidatorEvent) (bool, error) {
		entries = append(entries, EventEntry{Seq: key.K2(), Event: event})
		return false, nil
	})
	if err != nil {
		return nil, err
	}
	return entries, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-213)
```go
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
```

**File:** x/tieredrewards/keeper/msg_server.go (L257-263)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```
