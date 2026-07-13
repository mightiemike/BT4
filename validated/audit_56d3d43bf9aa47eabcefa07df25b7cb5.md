### Title
Bonus pool insufficiency unconditionally blocks all position exit paths, temporarily locking user principal — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`MsgLockTier` and `MsgCommitDelegationToTier` do not verify that the `RewardsPoolName` module account holds sufficient balance to cover future bonus rewards for the position's entire lock duration. When the pool is depleted — which occurs naturally every block via `topUpBaseRewards` — the exit handlers `MsgTierUndelegate` and `MsgExitTierWithDelegation` unconditionally call `claimRewards`, which fails with `ErrInsufficientBonusPool`. This prevents users from recovering their locked principal until the pool is externally refunded.

---

### Finding Description

**Entry validation does not check pool sufficiency.**

`validateNewPosition` in `x/tieredrewards/keeper/msg_validate.go` only enforces three conditions: non-vesting account, tier not close-only, and `amount >= MinLockAmount`. It performs no check on whether the rewards pool can cover future bonus obligations for the new position. [1](#0-0) 

**Exit paths unconditionally call `claimRewards`.**

Both `TierUndelegate` and `ExitTierWithDelegation` call `ms.claimRewards(ctx, pos)` before performing any state mutation. If this call fails, the entire transaction is rolled back and the user cannot exit. [2](#0-1) [3](#0-2) 

**`claimRewards` → `processEventsAndClaimBonus` → hard failure on empty pool.**

`processEventsAndClaimBonus` computes the accrued bonus and then calls `sufficientBonusPoolBalance`. If the pool balance is less than the owed bonus, it returns `ErrInsufficientBonusPool`, which propagates up through `claimRewards` to the message handler, causing the transaction to fail. [4](#0-3) [5](#0-4) 

**The pool is continuously drained every block.**

`topUpBaseRewards` in `abci.go` runs every `BeginBlock` and transfers tokens from `RewardsPoolName` to the distribution module to top up base staking rewards. If the pool is not continuously refunded by an external actor, it will eventually be empty. [6](#0-5) 

**The slash handler has a graceful fallback; user-facing exit handlers do not.**

The `slashRedelegationPosition` handler explicitly catches `ErrInsufficientBonusPool` and forfeits the bonus to avoid a chain halt. The user-facing `TierUndelegate` and `ExitTierWithDelegation` handlers have no equivalent fallback — the error propagates and the transaction fails. [7](#0-6) 

---

### Impact Explanation

When the `RewardsPoolName` module account is empty and a user has accrued non-zero bonus rewards, every exit path for their position fails:

- `MsgTierUndelegate` — fails, unbonding cannot start.
- `MsgExitTierWithDelegation` — fails, delegation cannot be transferred back.
- `MsgTierRedelegate` — fails, position cannot be moved.
- `MsgAddToTierPosition` — fails, additional funds cannot be added.
- `MsgClearPosition` — fails, exit cannot be cancelled.
- `MsgClaimTierRewards` — fails, rewards cannot be claimed.

The user's locked principal (the staking delegation held by the position's delegator address) is inaccessible until the pool is externally refunded. This is a temporary but complete lock of user funds.

The corrupted invariant: **a user who successfully locks tokens into a tier must always be able to exit after the exit commitment period elapses**. This invariant is broken whenever the pool is empty.

---

### Likelihood Explanation

The pool is drained every block by `topUpBaseRewards`. If governance does not continuously fund the pool, it will be empty. This is a realistic operational scenario — not a theoretical one. The pool has no minimum-balance enforcement at lock time, so users can lock tokens into tiers even when the pool is already empty. The test `TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient` confirms the failure mode is reachable. [8](#0-7) 

---

### Recommendation

Apply the same graceful fallback used in the slash handler to the user-facing exit paths. When `ErrInsufficientBonusPool` is returned during `claimRewards` inside `TierUndelegate` or `ExitTierWithDelegation`, forfeit the unpayable bonus (log it) and allow the exit to proceed. This ensures users can always recover their principal regardless of pool state, while the pool debt can be tracked and paid later. Alternatively, enforce a pool-sufficiency check at `validateNewPosition` time, rejecting new locks when the pool cannot cover the projected rewards for the position's full lock duration.

---

### Proof of Concept

1. The `RewardsPoolName` module account balance is zero (drained by `topUpBaseRewards` over time).
2. Alice calls `MsgLockTier` — succeeds, because `validateNewPosition` does not check pool balance.
3. Time passes; Alice accrues non-zero bonus rewards (`computeSegmentBonus` returns a positive value).
4. Alice's exit commitment elapses; she calls `MsgTierUndelegate`.
5. `TierUndelegate` calls `ms.claimRewards(ctx, pos)`.
6. `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` finds `poolBalance < bonusCoins` → returns `ErrInsufficientBonusPool`.
7. `TierUndelegate` returns the error; the transaction is rolled back.
8. Alice's delegation remains locked in the position's delegator address. She cannot undelegate, exit with delegation, redelegate, or clear her position.
9. Alice's principal is locked until an external actor funds the pool. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** x/tieredrewards/keeper/msg_validate.go (L28-42)
```go
func (k Keeper) validateNewPosition(ctx context.Context, owner string, amount math.Int, tier types.Tier) error {
	if err := k.validateNonVestingAccount(ctx, owner); err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	if !tier.MeetsMinLockRequirement(amount) {
		return types.ErrMinLockAmountNotMet
	}

	return nil
}
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

**File:** x/tieredrewards/keeper/msg_server.go (L518-535)
```go
func (ms msgServer) ExitTierWithDelegation(ctx context.Context, msg *types.MsgExitTierWithDelegation) (*types.MsgExitTierWithDelegationResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateExitTierWithDelegation(ctx, pos, msg.Owner, msg.Amount); err != nil {
		return nil, err
	}

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-232)
```go
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

**File:** x/tieredrewards/keeper/abci.go (L96-116)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}

	err = k.bankKeeper.SendCoinsFromModuleToModule(ctx, types.RewardsPoolName, distributiontypes.ModuleName, sdk.NewCoins(sdk.NewCoin(bondDenom, topUpAmount)))
	if err != nil {
		return err
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

**File:** x/tieredrewards/keeper/msg_server_claim_rewards_test.go (L100-133)
```go
// TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient verifies that ClaimTierRewards
// returns ErrInsufficientBonusPool when accrued bonus cannot be paid, so the tx rolls
// back and the user can retry later without losing base rewards to a partial claim.
func (s *KeeperSuite) TestMsgClaimTierRewards_FailsWhenBonusPoolInsufficient() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)
	delAddr := sdk.MustAccAddressFromBech32(pos.Owner)
	valAddr := sdk.MustValAddressFromBech32(pos.Delegation.ValidatorAddress)
	_, bondDenom := s.getStakingData()
	msgServer := keeper.NewMsgServerImpl(s.keeper)

	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	// Advance time and allocate base rewards, but intentionally leave bonus pool empty.
	s.ctx = s.ctx.WithBlockHeight(s.ctx.BlockHeight() + 1)
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(time.Hour * 24 * 365))
	s.allocateRewardsToValidator(valAddr, sdkmath.NewInt(100), bondDenom)

	// Bonus pool remains at 0 — bonus accrued but pool cannot cover it.
	balBefore := s.app.BankKeeper.GetBalance(s.ctx, delAddr, bondDenom)

	// Use a branched context so a failed message does not persist state (matches DeliverTx rollback).
	cacheCtx, _ := s.ctx.CacheContext()
	resp, err := msgServer.ClaimTierRewards(cacheCtx, &types.MsgClaimTierRewards{
		Owner:       delAddr.String(),
		PositionIds: []uint64{pos.Id},
	})
	s.Require().Error(err)
	s.Require().True(errors.Is(err, types.ErrInsufficientBonusPool))
	s.Require().Nil(resp)

	balAfter := s.app.BankKeeper.GetBalance(s.ctx, delAddr, bondDenom)
	s.Require().True(balAfter.Amount.Equal(balBefore.Amount), "failed claim must not transfer rewards")
}
```
