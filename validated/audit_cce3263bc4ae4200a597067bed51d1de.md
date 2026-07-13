### Title
Insufficient Bonus Pool Permanently Freezes User Principal and Rewards in All Exit Paths — (`x/tieredrewards/keeper/claim_rewards.go`)

### Summary

When the `RewardsPoolName` module account balance falls below the accrued bonus owed to a position, `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool`. Every user-facing exit and reward-claim path calls `claimRewards` unconditionally and propagates this error without a fallback, making it impossible for any delegator to undelegate, exit with delegation, redelegate, or claim rewards. The user's staked principal is frozen inside the position's delegator account for as long as the pool remains underfunded. The slash path already demonstrates the correct fix — it explicitly handles `ErrInsufficientBonusPool` by forgoing the bonus rather than aborting — but this pattern was never applied to the user-facing message handlers.

---

### Finding Description

`processEventsAndClaimBonus` computes the total bonus owed to a position and then calls `sufficientBonusPoolBalance` before transferring: [1](#0-0) 

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
```

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` whenever the pool balance is less than the computed bonus: [2](#0-1) 

This error propagates through `claimRewards`: [3](#0-2) 

Every user-facing message handler that needs to exit or claim calls `claimRewards` and returns the error unconditionally:

- **`TierUndelegate`** — [4](#0-3) 
- **`TierRedelegate`** — [5](#0-4) 
- **`ExitTierWithDelegation`** — [6](#0-5) 
- **`AddToTierPosition`** — [7](#0-6) 
- **`ClearPosition`** — [8](#0-7) 
- **`ClaimTierRewards`** — [9](#0-8) 

The slash path in `slashRedelegationPosition` already demonstrates the correct pattern: it catches `ErrInsufficientBonusPool` and forfeits the bonus rather than aborting: [10](#0-9) 

This asymmetry means the protocol tolerates a depleted pool during slashing (to prevent a chain halt) but does not extend the same tolerance to ordinary users trying to exit.

---

### Impact Explanation

**Impact: High.** When the `RewardsPoolName` module account is underfunded relative to any position's accrued bonus, that position's owner cannot execute `TierUndelegate`, `ExitTierWithDelegation`, or `TierRedelegate`. Because all three exit paths call `claimRewards` before touching the delegation, the user's staked principal — held in the position's isolated delegator account — is completely frozen. The user cannot recover their tokens until governance replenishes the pool. Accrued base rewards (routed via `x/distribution`) are also inaccessible through the module's claim path during this window. [3](#0-2) 

---

### Likelihood Explanation

**Likelihood: Medium.** The bonus pool is a finite module account (`types.RewardsPoolName`) funded externally (e.g., by governance or inflation). As positions accrue bonus over time, the pool balance decreases. A governance delay in replenishment, an unexpected spike in accrued bonus (e.g., many positions reaching their `ExitUnlockAt` simultaneously), or a misconfigured inflation schedule can all deplete the pool. No attacker action is required; normal protocol operation is sufficient to trigger the condition. [2](#0-1) 

---

### Recommendation

Mirror the pattern already used in `slashRedelegationPosition`: in `claimRewards` (or in each message handler), catch `ErrInsufficientBonusPool` and proceed with zero bonus rather than returning an error. This ensures that exit and undelegation paths remain available regardless of pool balance, while still paying the bonus whenever the pool is solvent.

```go
bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
if err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        bonus = sdk.NewCoins() // forfeit bonus, allow exit
    } else {
        return types.PositionState{}, nil, nil, err
    }
}
``` [10](#0-9) 

---

### Proof of Concept

1. A delegator creates a position via `MsgLockTier` and accrues bonus over time.
2. The `RewardsPoolName` module account balance drops below the delegator's accrued bonus (pool depleted by prior claims or never replenished).
3. The delegator submits `MsgTierUndelegate` (or `MsgExitTierWithDelegation`).
4. The handler calls `ms.claimRewards(ctx, pos)` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → returns `ErrInsufficientBonusPool`.
5. The transaction is rejected with the error; the delegation is untouched.
6. The delegator's principal remains locked in the position's delegator account with no available exit path until governance funds the pool. [11](#0-10) [12](#0-11)

### Citations

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L219-232)
```go
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

**File:** x/tieredrewards/keeper/msg_server.go (L229-232)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L314-317)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L406-409)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L448-451)
```go
	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
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
