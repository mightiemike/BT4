### Title
Migration Halted by `ErrInsufficientBonusPool` in `ForceFullExitWithDelegation` During v8 Upgrade — (`x/tieredrewards/keeper/force_exit.go`, `x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

`Migrate1to2` iterates all vesting-owned positions and calls `ForceFullExitWithDelegation` for each. That function calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance`. If the `RewardsPoolName` module account balance is insufficient to cover a position's accumulated bonus, `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`. This error propagates unhandled through every frame of the migration call stack, causing the upgrade handler to return an error and halt the chain upgrade. All remaining vesting-owned positions stay locked.

---

### Finding Description

**Call chain:**

```
Migrate1to2 (migrations.go:17)
  └─ v2.Migrate (v2/migrate.go:28)
       └─ exitVestedAccountsPositions (v2/migrate.go:71)
            └─ ForceFullExitWithDelegation (force_exit.go:14)
                 └─ claimRewards (claim_rewards.go:87)
                      └─ processEventsAndClaimBonus (claim_rewards.go:142)
                           └─ sufficientBonusPoolBalance (bonus_rewards.go:48)
                                └─ ErrInsufficientBonusPool  ← propagates all the way up
```

**`sufficientBonusPoolBalance`** returns a hard error when the pool balance is below the computed bonus: [1](#0-0) 

**`processEventsAndClaimBonus`** propagates it without any special-case handling: [2](#0-1) 

**`claimRewards`** propagates it: [3](#0-2) 

**`ForceFullExitWithDelegation`** propagates it: [4](#0-3) 

**`exitVestedAccountsPositions`** propagates it: [5](#0-4) 

**`v2.Migrate`** propagates it: [6](#0-5) 

**`Migrate1to2`** is registered as the module's consensus-version migration handler: [7](#0-6) 

A non-nil return from a registered migration handler causes the Cosmos SDK upgrade manager to abort the upgrade block.

---

### Impact Explanation

The v8 chain upgrade is halted at the block where the upgrade handler runs. Every vesting-owned position that has not yet been processed remains locked in the tieredrewards module. The chain cannot advance past the upgrade height until the binary is patched and the upgrade re-run (which requires a coordinated validator emergency patch). No user funds are directly stolen, but all vesting-owned positions are permanently locked until the chain is recovered.

---

### Likelihood Explanation

The precondition is realistic and can be reached without any privileged action:

1. Multiple vesting accounts hold tier positions (a normal user action via `MsgLockTier` or `MsgCommitDelegationToTier`).
2. The bonus pool is partially drained by earlier positions processed in the same `exitVestedAccountsPositions` loop — each successful `ForceFullExitWithDelegation` call transfers bonus out of the pool via `SendCoinsFromModuleToAccount`.
3. When the pool balance drops below the next position's accumulated bonus, the migration aborts.

The developers were aware of this risk pattern: `slashRedelegationPosition` explicitly catches `ErrInsufficientBonusPool` and silently forfeits the bonus to avoid a chain halt: [8](#0-7) 

The ADR also documents this asymmetry — user-driven paths fail atomically (user retries), but the migration path has no equivalent guard. The same protection was not applied to `ForceFullExitWithDelegation`. [9](#0-8) 

---

### Recommendation

Apply the same "forfeit bonus silently on `ErrInsufficientBonusPool`" pattern used in `slashRedelegationPosition` to `ForceFullExitWithDelegation`. When `claimRewards` (or `processEventsAndClaimBonus` directly) returns `ErrInsufficientBonusPool` during migration, log the event and continue without paying the bonus rather than propagating the error. The position exit (delegation transfer + deletion) must still complete.

---

### Proof of Concept

1. Deploy chain with tieredrewards module at consensus version 1.
2. Create N vesting accounts, each with a tier position accumulating bonus over time (advance block time).
3. Fund the `RewardsPoolName` module account with an amount sufficient to cover positions 1..N-1 but not position N.
4. Trigger the v8 upgrade (run `Migrate1to2`).
5. Observe: positions 1..N-1 exit successfully, draining the pool; position N causes `sufficientBonusPoolBalance` to return `ErrInsufficientBonusPool`; `exitVestedAccountsPositions` returns an error; `Migrate1to2` returns an error; the upgrade handler aborts.
6. Assert: upgrade block panics/errors; position N and any subsequent positions remain in state; chain is halted. [10](#0-9) [11](#0-10)

### Citations

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-100)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/force_exit.go (L14-45)
```go
func (k Keeper) ForceFullExitWithDelegation(ctx context.Context, posID uint64) error {
	logger := k.logger(ctx)
	logger.Info("force-exit: begin", "position_id", posID)

	posState, err := k.getPositionState(ctx, posID)
	if err != nil {
		return fmt.Errorf("get position %d: %w", posID, err)
	}
	if !posState.IsDelegated() {
		logger.Error("force-exit: position is not delegated; cannot force full exit",
			"position_id", posID,
			"owner", posState.Owner,
		)
		return nil
	}
	logger.Info("force-exit: position state loaded",
		"position_id", posID,
		"owner", posState.Owner,
		"tier_id", posState.TierId,
		"validator", posState.Delegation.ValidatorAddress,
		"shares", posState.Delegation.Shares.String(),
	)

	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
	logger.Info("force-exit: claimed rewards",
		"position_id", posID,
		"base_rewards", baseRewards.String(),
		"bonus_rewards", bonusRewards.String(),
	)
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L37-39)
```go
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L71-105)
```go
func exitVestedAccountsPositions(
	ctx context.Context,
	positions collections.Map[uint64, types.Position],
	ak AccountKeeper,
	pk PositionForceExiter,
) error {
	sdkCtx := sdk.UnwrapSDKContext(ctx)

	var toExit []uint64
	if err := positions.Walk(ctx, nil, func(posID uint64, pos types.Position) (bool, error) {
		ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
		if err != nil {
			return false, fmt.Errorf("parse owner address for position %d: %w", posID, err)
		}
		acc := ak.GetAccount(ctx, ownerAddr)
		if acc == nil {
			return false, fmt.Errorf("owner account not found for position %d: %s", posID, ownerAddr.String())
		}
		if _, ok := acc.(sdkvesting.VestingAccount); ok {
			toExit = append(toExit, posID)
		}
		return false, nil
	}); err != nil {
		return fmt.Errorf("walk positions: %w", err)
	}

	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
	}

	sdkCtx.Logger().Info("v8 migration: vesting-owned positions exited", "count", len(toExit))
	return nil
```

**File:** x/tieredrewards/module.go (L105-108)
```go
	migrator := keeper.NewMigrator(am.keeper)
	if err := cfg.RegisterMigration(types.ModuleName, 1, migrator.Migrate1to2); err != nil {
		panic(fmt.Errorf("failed to register tieredrewards migration v1->v2: %w", err))
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

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```
