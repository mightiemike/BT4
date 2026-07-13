### Title
Duplicate Position IDs in `ClaimTierRewards` Enable Double-Claim of Bonus Rewards and Corrupt Validator Event Reference Counts — (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

`MsgClaimTierRewards` accepts a caller-supplied slice of position IDs. The handler loads all positions into memory **before** processing any of them, and performs no deduplication. If the same position ID appears twice in `msg.PositionIds`, the position is loaded twice with identical initial state. The second processing re-reads and re-pays validator-event bonus rewards that were already disbursed in the first pass, and double-decrements event reference counts, corrupting the accounting for every other position delegated to the same validator.

---

### Finding Description

`ClaimTierRewards` in `x/tieredrewards/keeper/msg_server.go` first collects all requested positions:

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)
    ...
    positions = append(positions, pos)
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
``` [1](#0-0) 

`validateClaimRewards` only verifies ownership; it does not reject duplicate IDs. [2](#0-1) 

`claimRewardsAndUpdatesPositions` then iterates the slice sequentially:

```go
for i := range positions {
    pos := &positions[i]
    base, err := k.claimBaseRewards(ctx, *pos)
    bonus, err := k.processEventsAndClaimBonus(ctx, pos)
    if err := k.setPosition(ctx, pos.Position, nil); err != nil { ... }
}
``` [3](#0-2) 

`processEventsAndClaimBonus` reads validator events from the store using `pos.LastEventSeq` (the value captured at load time), computes bonus, calls `bankKeeper.SendCoinsFromModuleToAccount` to pay it, and calls `decrementEventRefCount` for each event: [4](#0-3) [5](#0-4) 

When the same position ID appears twice:

1. **First pass** — events are fetched, bonus is paid, ref counts are decremented, `setPosition` writes the updated `LastEventSeq` to the store.
2. **Second pass** — `positions[1]` still holds the **original** `LastEventSeq` (captured before any processing). `getValidatorEventsSince` re-fetches events that still exist in the store (those whose ref count did not reach zero in pass 1, i.e., events shared with other positions). Bonus is paid **again** for those events. `decrementEventRefCount` is called **again** for each of them.

---

### Impact Explanation

**Double-payment of bonus rewards.** Any validator event with ref count ≥ 2 (meaning at least one other position is also delegated to the same validator) survives the first pass and is re-processed in the second pass. `bankKeeper.SendCoinsFromModuleToAccount` transfers bonus coins from `types.RewardsPoolName` to the attacker's address twice for those events, draining the shared bonus pool.

**Premature deletion of validator events.** Each double-decrement reduces an event's ref count by 2 instead of 1. An event with ref count exactly 2 is deleted after the attacker's transaction, even though one legitimate position still references it. When that other position later calls `ClaimTierRewards`, `getValidatorEventsSince` returns no entry for the deleted event; the position silently skips it and permanently loses the corresponding bonus rewards.

The corrupted value is the `RewardsPoolName` module-account balance and the per-validator event ref-count store entries. [6](#0-5) 

---

### Likelihood Explanation

Any position owner can craft a `MsgClaimTierRewards` transaction with a repeated position ID (e.g., `PositionIds: [42, 42]`). No special privilege is required. The precondition — at least one other position delegated to the same validator — is the normal operating state of any active validator. The attack is therefore trivially reachable on mainnet.

---

### Recommendation

1. **Deduplicate `msg.PositionIds` in `MsgClaimTierRewards.Validate()`** — reject the message if any ID appears more than once.
2. **Re-read position state from the store inside `claimRewardsAndUpdatesPositions`** rather than operating on a pre-loaded snapshot, so that the second processing of any position sees the already-updated `LastEventSeq` and finds no new events to pay.
3. As a defence-in-depth measure, assert inside `processEventsAndClaimBonus` that `pos.LastEventSeq` has not already been advanced past the events being processed (i.e., guard against stale snapshots).

---

### Proof of Concept

1. Attacker owns position ID `42`, delegated to validator `V`. At least one other position (`43`) is also delegated to `V`. Both positions have a non-zero `LastEventSeq` gap (pending bonus events with ref count ≥ 2).
2. Attacker submits:
   ```
   MsgClaimTierRewards { Owner: attacker, PositionIds: [42, 42] }
   ```
3. Handler loads `positions[0]` and `positions[1]` — both are identical snapshots of position 42 with the same `LastEventSeq = S`.
4. **Pass 1 (positions[0]):** `processEventsAndClaimBonus` fetches events since `S`, pays bonus `B` to attacker, decrements ref counts (events shared with position 43 go from 2 → 1), writes updated `LastEventSeq = S'` via `setPosition`.
5. **Pass 2 (positions[1]):** `processEventsAndClaimBonus` fetches events since `S` again (shared events still exist with ref count 1), pays bonus `B` to attacker **again**, decrements ref counts (shared events go from 1 → 0, **deleted**), writes `LastEventSeq = S'` again.
6. Attacker receives `2B` bonus instead of `B`. Position 43's events are deleted; when position 43's owner later claims, they receive zero bonus for those events. [7](#0-6) [3](#0-2) [8](#0-7)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-468)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventTierRewardsClaimed{
		Owner:        msg.Owner,
		PositionIds:  msg.PositionIds,
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
	}); err != nil {
		return nil, err
	}

	return &types.MsgClaimTierRewardsResponse{
		BaseRewards:  totalBase,
		BonusRewards: totalBonus,
		PositionIds:  msg.PositionIds,
	}, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L106-135)
```go
func (k Keeper) claimRewardsAndUpdatesPositions(ctx context.Context, positions []types.PositionState) (sdk.Coins, sdk.Coins, error) {
	totalBase := sdk.NewCoins()
	totalBonus := sdk.NewCoins()

	for i := range positions {
		pos := &positions[i]

		if !pos.IsDelegated() {
			continue
		}

		base, err := k.claimBaseRewards(ctx, *pos)
		if err != nil {
			return nil, nil, err
		}
		totalBase = totalBase.Add(base...)

		bonus, err := k.processEventsAndClaimBonus(ctx, pos)
		if err != nil {
			return nil, nil, err
		}
		totalBonus = totalBonus.Add(bonus...)

		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, nil, err
		}
	}

	return totalBase, totalBonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-251)
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

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventBonusRewardsClaimed{
		PositionId: pos.Id,
		Owner:      pos.Owner,
		Rewards:    bonusCoins,
	}); err != nil {
		return nil, err
	}

	return bonusCoins, nil
```
