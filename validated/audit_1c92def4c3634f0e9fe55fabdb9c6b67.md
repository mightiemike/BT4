### Title
Unbounded validator-event loop in `processEventsAndClaimBonus` allows a validator to permanently lock position holders' funds — (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` iterates over every `ValidatorEvent` accumulated since a position's `LastEventSeq` with no upper bound. A validator can repeatedly cycle through bonded → unbonding → bonded state transitions (downtime-jail / unjail), appending an unbounded number of `UNBOND` and `BOND` events to the log. Because every user-facing exit path settles rewards first, position holders on that validator eventually cannot exit, redelegate, or undelegate — their locked tokens become permanently inaccessible.

---

### Finding Description

**Unbounded loop in `processEventsAndClaimBonus`**

`getValidatorEventsSince` returns every stored event with `seq > pos.LastEventSeq` and `processEventsAndClaimBonus` iterates the full slice with no pagination or cap:

```go
events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
...
for _, entry := range events {   // no limit
    ...
    if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil { ... }
}
``` [1](#0-0) 

**Unbounded event accumulation via staking hooks**

Three staking hooks append a new `ValidatorEvent` entry every time a validator changes state. There is no cap on how many events a validator may accumulate:

```go
func (h Hooks) AfterValidatorBeginUnbonding(...) error {
    ...
    _, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
        EventType: types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND, ...
    })
}
func (h Hooks) AfterValidatorBonded(...) error {
    ...
    _, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
        EventType: types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND, ...
    })
}
``` [2](#0-1) 

**Every exit path settles rewards first**

`MsgTierUndelegate`, `MsgTierRedelegate`, `MsgAddToTierPosition`, `MsgClearPosition`, and `MsgExitTierWithDelegation` all call `claimRewards` → `processEventsAndClaimBonus` before performing their state mutation. There is no bypass path that skips event processing. [3](#0-2) 

**`getValidatorEventsSince` walks the full range**

```go
func (k Keeper) getValidatorEventsSince(...) ([]EventEntry, error) {
    rng := collections.NewPrefixedPairRange[sdk.ValAddress, uint64](valAddr).
        StartExclusive(startSeq)
    var entries []EventEntry
    err := k.ValidatorEvents.Walk(ctx, rng, func(...) (bool, error) {
        entries = append(entries, ...)
        return false, nil   // never stops early
    })
    ...
}
``` [4](#0-3) 

---

### Impact Explanation

Once the event log for a validator grows beyond the gas budget of a single transaction, every operation that settles rewards for a position on that validator fails with out-of-gas. Because tier positions are locked (no undelegation until `ExitUnlockAt` elapses), and because every exit path — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgTierRedelegate` — calls `processEventsAndClaimBonus` first, position holders are permanently unable to recover their staked tokens. The corrupted value is the position holder's locked delegation (their staked bond-denom tokens held via the per-position delegator address).

---

### Likelihood Explanation

A validator can trigger the attack by repeatedly going offline long enough to be jailed for downtime, then calling `MsgUnjail`. Each jail/unjail cycle fires `AfterValidatorBeginUnbonding` (UNBOND event) and `AfterValidatorBonded` (BOND event). The cost per cycle is a small downtime-slash fraction (typically 0.01 % of self-stake) plus the unjail gas fee. Tier positions are locked, so position holders cannot redelegate away from the attacker validator before the exit lock elapses. A validator who deliberately targets positions locked for 1–5 years has a long window to accumulate events. The attack is not free, but the per-cycle cost is low relative to the value of locked positions.

---

### Recommendation

1. **Cap events processed per call.** Introduce a `MaxEventsPerClaim` constant (e.g. 200). If `getValidatorEventsSince` returns more entries than the cap, process only up to the cap and persist the updated `LastEventSeq` so the next call continues from where the previous one stopped. This turns one potentially unbounded call into a series of bounded calls.

2. **Alternatively, enforce a maximum event backlog per validator.** When `appendValidatorEvent` would push the backlog above a threshold, reject the append (or force-settle all positions first). This prevents the log from growing unboundedly in the first place.

---

### Proof of Concept

1. Governance creates a tier with a multi-year `ExitDuration`. Many users call `MsgLockTier` on validator V, creating N delegated positions. All positions have `LastEventSeq = 0`.

2. Attacker (operator of V) repeatedly goes offline past the downtime threshold, triggering `BeforeValidatorSlashed` (SLASH event) + `AfterValidatorBeginUnbonding` (UNBOND event), then calls `MsgUnjail`, triggering `AfterValidatorBonded` (BOND event). Each cycle appends 3 events. After K cycles, `ValidatorEventSeq[V] = 3K`. [5](#0-4) 

3. A position holder whose `LastEventSeq = 0` now has 3K events to process. They call `MsgTierUndelegate` (or any other exit message). The message handler calls `claimRewards` → `processEventsAndClaimBonus`, which loads all 3K events and iterates them. For large K this exceeds the per-transaction gas limit and the transaction aborts. [6](#0-5) 

4. The position holder retries with maximum gas — the transaction still fails. They cannot call `MsgTierRedelegate` (same code path), `MsgExitTierWithDelegation` (same), or `MsgClaimTierRewards` (same). Their locked tokens are permanently inaccessible. [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L48-80)
```go
// claimRewardsAndUpdateTierPositions claims base and bonus rewards for all
// delegated positions in the given tier.
func (k Keeper) claimRewardsAndUpdateTierPositions(ctx context.Context, tierId uint32) error {
	ids, err := k.getPositionsIdsByTier(ctx, tierId)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}

	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L87-103)
```go
func (k Keeper) claimRewards(ctx context.Context, pos types.PositionState) (types.PositionState, sdk.Coins, sdk.Coins, error) {
	if !pos.IsDelegated() {
		return pos, sdk.NewCoins(), sdk.NewCoins(), nil
	}

	base, err := k.claimBaseRewards(ctx, pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}

	return pos, base, bonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-199)
```go
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
```

**File:** x/tieredrewards/keeper/hooks.go (L27-75)
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
}

// AfterValidatorBonded records a BOND event.
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
```

**File:** x/tieredrewards/keeper/hooks.go (L100-122)
```go
func (h Hooks) BeforeValidatorSlashed(ctx context.Context, valAddr sdk.ValAddress, fraction sdkmath.LegacyDec) error {
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
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
	return err
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
