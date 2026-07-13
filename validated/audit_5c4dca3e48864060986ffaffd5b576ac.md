### Title
Depleted Bonus Pool Unconditionally Blocks All Exit Paths for Positions with Accrued Rewards - (File: x/tieredrewards/keeper/claim_rewards.go)

### Summary
When the `x/tieredrewards` bonus pool is depleted, every exit-path message — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgClearPosition`, and `MsgTierRedelegate` — fails atomically because each unconditionally calls `claimRewards` before performing the exit operation. Any position that has accrued non-zero bonus rewards is completely locked: the owner cannot undelegate, exit with delegation, cancel an exit, or redelegate until an external party replenishes the pool. There is no mechanism to forfeit bonus and exit anyway.

### Finding Description
Every mutation message that moves tokens out of the module calls `claimRewards` as its first substantive step:

- `TierUndelegate` — `msg_server.go:166`: `pos, _, _, err = ms.claimRewards(ctx, pos)`
- `TierRedelegate` — `msg_server.go:229`: `pos, _, _, err = ms.claimRewards(ctx, pos)`
- `ClearPosition` — `msg_server.go:406`: `pos, _, _, err = ms.claimRewards(ctx, pos)`
- `ExitTierWithDelegation` — `msg_server.go:532`: `pos, _, _, err = ms.claimRewards(ctx, pos)` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Inside `claimRewards`, `processEventsAndClaimBonus` computes the accrued bonus and then calls `sufficientBonusPoolBalance`. When the pool balance is less than the accrued bonus, it returns `ErrInsufficientBonusPool`, which propagates back through `claimRewards` as a hard error: [5](#0-4) [6](#0-5) [7](#0-6) 

The pool check is only skipped when `totalBonus.IsZero()`: [8](#0-7) 

Any position that has been delegated to a bonded validator for a non-trivial duration will have a positive `totalBonus`, so the pool check fires. The ADR explicitly documents this as the intended behavior for user-driven paths:

> *"User-driven paths (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished."* [9](#0-8) 

However, the design conflates two distinct concerns: (a) paying out earned rewards and (b) releasing the user's principal. The result is that a depleted pool — a normal operational state — becomes a global lock on all exit paths for every position with accrued bonus.

Contrast this with the `BeforeRedelegationSlashed` hook, which explicitly forfeits bonus silently when the pool is insufficient to avoid a chain halt: [10](#0-9) 

No equivalent escape hatch exists for user-initiated exit messages.

### Impact Explanation
When the bonus pool is depleted:

1. **`MsgTierUndelegate` fails** — the user cannot start the 21-day unbonding period; their principal remains locked in the module.
2. **`MsgExitTierWithDelegation` fails** — the instant-exit path is also blocked.
3. **`MsgClearPosition` fails** — the user cannot cancel a triggered exit to redelegate or add tokens.
4. **`MsgTierRedelegate` fails** — the user cannot move to a safer validator.

The only message that does not call `claimRewards` and therefore still works is `MsgTriggerExitFromTier` and `MsgWithdrawFromTier` (the latter only applies after unbonding completes, which requires `TierUndelegate` to have already succeeded). `MsgWithdrawFromTier` does not call `claimRewards`, but it is unreachable if `TierUndelegate` was never able to execute.

During the lockout window the validator can be slashed or jailed, compounding the user's loss. The user has no recourse: they cannot redelegate to a safer validator, cannot cancel an exit, and cannot undelegate. Their entire principal is held hostage to the pool balance.

The corrupted invariant is: *"a position owner can always initiate exit of their own staked tokens."* The corrupted value is the staked principal held at the per-position delegator address, which becomes inaccessible.

### Likelihood Explanation
The bonus pool is funded by external transfers to the module account. It is depleted continuously as rewards are paid out across all positions. There is no on-chain mechanism that guarantees the pool stays funded; it depends entirely on off-chain operational discipline. As the protocol matures and the number of positions grows, the pool drains faster. A single period of inattention — or a deliberate decision not to top up — puts every position with accrued bonus into the locked state simultaneously. This is a realistic, non-adversarial operational scenario.

### Recommendation
Decouple reward settlement from exit authorization. When the bonus pool is insufficient, exit-path messages (`TierUndelegate`, `ExitTierWithDelegation`, `ClearPosition`, `TierRedelegate`) should silently forfeit the unpayable bonus (advancing `LastBonusAccrual` and `LastEventSeq` without transferring coins) and proceed with the exit, mirroring the behavior already implemented in `BeforeRedelegationSlashed`:

```go
// In claimRewards (or a new claimRewardsForExit variant):
bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
if errors.Is(err, types.ErrInsufficientBonusPool) {
    // Forfeit bonus, advance checkpoints, continue with exit.
    applyBonusAccrualCheckpoint(&pos.Position, blockTime)
    bonus = sdk.NewCoins()
} else if err != nil {
    return types.PositionState{}, nil, nil, err
}
```

Alternatively, introduce a separate `claimRewardsForExit` helper used only by exit-path messages that applies this forfeiture logic, keeping the strict failure behavior for `ClaimTierRewards` and `AddToTierPosition` where the user is not trying to exit.

### Proof of Concept

1. User calls `MsgLockTier` with `amount = tier.MinLockAmount`, delegating to a bonded validator. Position is created with `LastBonusAccrual = block_time`.
2. Chain advances by 365 days. Bonus accrues: `shares × tokensPerShare × bonusApy × 365d / year > 0`.
3. Bonus pool balance reaches zero (drained by prior reward claims from other positions, or simply never replenished).
4. User's validator begins exhibiting downtime. User submits `MsgTierRedelegate` to move to a safer validator.
5. `TierRedelegate` calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → pool is empty → returns `ErrInsufficientBonusPool`.
6. Transaction fails. User cannot redelegate.
7. User submits `MsgTierUndelegate` (exit elapsed). Same failure path. Cannot undelegate.
8. User submits `MsgExitTierWithDelegation`. Same failure path. Cannot exit.
9. Validator is slashed. User's principal is reduced. User had no way to exit before the slash.
10. User's tokens remain locked until an external party sends funds to the rewards pool module account. [5](#0-4) [11](#0-10) [12](#0-11) [13](#0-12)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L152-208)
```go
func (ms msgServer) TierUndelegate(ctx context.Context, msg *types.MsgTierUndelegate) (*types.MsgTierUndelegateResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateUndelegatePosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	srcValidator := pos.Delegation.ValidatorAddress
	valAddr, err := sdk.ValAddressFromBech32(srcValidator)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
	}

	pos.ClearBonusCheckpoints()

	if err := ms.setPosition(ctx, pos.Position, &ValidatorTransition{PreviousAddress: srcValidator}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionUndelegated{
		PositionId:     pos.Id,
		TierId:         pos.TierId,
		Owner:          pos.Owner,
		Validator:      srcValidator,
		CompletionTime: completionTime,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTierUndelegateResponse{
		CompletionTime: completionTime,
		PositionId:     pos.Id,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L228-232)
```go

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L405-409)
```go

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L531-535)
```go

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
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

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

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

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
