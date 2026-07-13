### Title
Duplicate Position IDs in `MsgClaimTierRewards` Enable Double-Claiming of Bonus Rewards - (`x/tieredrewards/keeper/msg_server.go`)

### Summary
The `ClaimTierRewards` message handler in `x/tieredrewards/keeper/msg_server.go` loads all requested positions into a slice before processing them, with no deduplication check on the input `PositionIds` list. If a caller submits the same position ID twice, the position state is loaded twice with identical stale checkpoints. The second processing re-computes and re-pays the same bonus reward segment, draining the `RewardsPoolName` module account beyond what is legitimately owed.

### Finding Description

In `ClaimTierRewards`, the handler iterates over `msg.PositionIds` and appends each loaded `PositionState` to a slice: [1](#0-0) 

There is no uniqueness check on `posId` values. If the same ID appears twice, both entries in the `positions` slice hold the same initial state — identical `LastBonusAccrual`, `LastEventSeq`, and `LastKnownBonded` values.

`claimRewardsAndUpdatesPositions` then iterates over the slice and processes each entry independently: [2](#0-1) 

For each entry, `processEventsAndClaimBonus` is called. It initialises `segmentStart` from the in-memory (stale) `pos.LastBonusAccrual` and walks validator events since `pos.LastEventSeq`: [3](#0-2) 

After the event loop, the **final open segment** is always computed from `segmentStart` (= stale `LastBonusAccrual`) to `blockTime` using the live validator rate: [4](#0-3) 

When the first copy of the position is processed, this segment is paid and `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` to `blockTime` in memory, then `setPosition` persists it. When the second copy is processed, it starts from the same stale `LastBonusAccrual` (loaded before the first copy was persisted), so the identical final segment is computed and paid again. The `sufficientBonusPoolBalance` guard only checks whether the pool can cover the current iteration's payout — it does not prevent the same segment from being paid N times across N duplicate entries. [5](#0-4) 

The bonus is transferred from the `RewardsPoolName` module account to the position owner: [6](#0-5) 

### Impact Explanation

A position owner can drain the `tieredrewards` bonus rewards pool by submitting a single `MsgClaimTierRewards` transaction with their position ID repeated N times. Each repetition pays out the full accrued bonus for the open segment `[LastBonusAccrual, blockTime]`. The total overpayment is `(N-1) × legitimate_bonus`. The `RewardsPoolName` module account balance is the only practical cap. All other position owners are harmed because the pool that funds their future bonus rewards is depleted.

### Likelihood Explanation

Any delegator who holds at least one tiered-rewards position can trigger this. The attack requires only a standard signed transaction with a repeated field value — no privileged role, no leaked key, no social engineering. The `MsgClaimTierRewards` message is a normal production entrypoint reachable by any account. [7](#0-6) 

### Recommendation

Add a deduplication check on `msg.PositionIds` before loading positions. Either:

1. Reject the message in `Validate()` (in `msg_validate.go`) if any position ID appears more than once, or
2. Use a `map[uint64]struct{}` in the handler loop to skip already-processed IDs before appending to the `positions` slice.

The fix should be applied at the message-validation layer so it is enforced before any state reads occur.

### Proof of Concept

1. Alice holds position ID `42` in a bonded tier with accrued bonus `B` for the current open segment.
2. Alice submits `MsgClaimTierRewards{Owner: alice, PositionIds: [42, 42, 42, ..., 42]}` (N repetitions).
3. The handler loads position `42` N times into the `positions` slice, each copy with `LastBonusAccrual = T_prev`.
4. `claimRewardsAndUpdatesPositions` iterates:
   - Iteration 1: pays `B`, persists `LastBonusAccrual = blockTime`.
   - Iteration 2: stale copy still has `LastBonusAccrual = T_prev`; pays `B` again.
   - … repeated N times.
5. Alice receives `N × B` instead of `B`, draining `(N-1) × B` from the rewards pool. [8](#0-7) [9](#0-8)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-215)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-241)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
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
