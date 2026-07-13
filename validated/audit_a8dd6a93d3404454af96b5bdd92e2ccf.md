Now I have the complete picture. Let me trace every link in the call chain and confirm the missing guard.

**Complete call chain:**

1. `app/upgrades.go:58-60` → `RunMigrations` (errors abort the upgrade)
2. `x/tieredrewards/module.go:106` → `Migrate1to2` registered as v1→v2 migration
3. `x/tieredrewards/keeper/migrations.go:17-18` → calls `v2.Migrate`
4. `x/tieredrewards/migrations/v2/migrate.go:37-39` → calls `exitVestedAccountsPositions`, propagates error
5. `x/tieredrewards/migrations/v2/migrate.go:99-101` → calls `ForceFullExitWithDelegation`, propagates error
6. `x/tieredrewards/keeper/force_exit.go:37-40` → calls `claimRewards`, propagates error **with no special handling**
7. `x/tieredrewards/keeper/claim_rewards.go:97-99` → calls `processEventsAndClaimBonus`, propagates error
8. `x/tieredrewards/keeper/claim_rewards.go:230-232` → calls `sufficientBonusPoolBalance`, returns `ErrInsufficientBonusPool`

**The missing guard vs. the existing guard:**

- `x/tieredrewards/keeper/slash.go:54-63` — `slashRedelegationPosition` **explicitly catches** `ErrInsufficientBonusPool` and logs it instead of returning it, with the comment *"Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt."*
- `x/tieredrewards/keeper/force_exit.go:37-40` — `ForceFullExitWithDelegation` has **no such guard**; the error is wrapped and returned unconditionally.

**The integration test confirms the dependency:**
`integration_tests/test_upgrade_v8.py:124-126` explicitly funds the pool (`fund_pool(cluster, "signer1", f"50000000{DENOM}")`) before triggering the upgrade — a manual workaround that is not enforced by the code.

---

### Title
Insufficient Bonus Pool at Upgrade Time Aborts v8 Migration, Permanently Halting the Chain — (`x/tieredrewards/migrations/v2/migrate.go`, `x/tieredrewards/keeper/force_exit.go`)

### Summary
`ForceFullExitWithDelegation` propagates `ErrInsufficientBonusPool` unconditionally. When called from the v8 upgrade migration for any vesting-owned position whose accrued bonus exceeds the pool balance, the error bubbles all the way up through `exitVestedAccountsPositions` → `Migrate` → `Migrate1to2` → `RunMigrations` → the upgrade handler, aborting the upgrade and permanently halting the chain.

### Finding Description

The v8 upgrade handler calls `RunMigrations`, which invokes `Migrate1to2`: [1](#0-0) [2](#0-1) 

`Migrate1to2` calls `v2.Migrate`, which calls `exitVestedAccountsPositions`: [3](#0-2) 

`exitVestedAccountsPositions` iterates all vesting-owned positions and calls `ForceFullExitWithDelegation` for each, propagating any error: [4](#0-3) 

`ForceFullExitWithDelegation` calls `claimRewards` and propagates its error with no special handling: [5](#0-4) 

`claimRewards` calls `processEventsAndClaimBonus`: [6](#0-5) 

`processEventsAndClaimBonus` calls `sufficientBonusPoolBalance`, which returns `ErrInsufficientBonusPool` if the pool cannot cover the computed bonus: [7](#0-6) [8](#0-7) 

The codebase already demonstrates the correct pattern for this exact error in the slash hook: [9](#0-8) 

`slashRedelegationPosition` catches `ErrInsufficientBonusPool` and logs it rather than returning it, explicitly to prevent chain halt. `ForceFullExitWithDelegation` has no equivalent guard.

The integration test works around this by manually funding the pool before the upgrade: [10](#0-9) 

This is not enforced by the migration code itself.

### Impact Explanation

If the bonus pool balance is less than the accrued bonus for any vesting-owned position at upgrade time, the entire v8 upgrade handler aborts. Because Cosmos SDK upgrade handlers are not retryable once the upgrade height is reached without a coordinated chain restart and patch, this results in a permanent chain halt. All user funds held in the tieredrewards module (delegations, unbonding amounts, bank balances at position delegator addresses) become inaccessible.

### Likelihood Explanation

The pool is a shared resource funded by governance. It can be empty or insufficient at upgrade time due to:
1. The pool never being funded (or being underfunded relative to total accrued bonus across all vesting positions).
2. Legitimate reward claims by other position owners draining the pool between the funding transaction and the upgrade block.
3. A deliberate attacker claiming rewards to drain the pool immediately before the upgrade height.

Vesting-owned positions are long-running (they predate the `ErrVestingAccountNotAllowed` restriction added in this same upgrade), so their accrued bonus can be substantial. The pool balance required to cover all of them is not enforced or checked anywhere before the upgrade executes.

### Recommendation

Apply the same `ErrInsufficientBonusPool` guard used in `slashRedelegationPosition` to `ForceFullExitWithDelegation`: catch the error, log it, forfeit the bonus for that position, and continue. The delegation transfer and position deletion must still proceed so the migration completes regardless of pool state.

### Proof of Concept

1. Seed a vesting-owned position with a large `LastBonusAccrual` gap (e.g., position created months before the upgrade with a high-APY tier).
2. Leave the bonus pool at zero (or drain it via `MsgClaimTierRewards` on other positions).
3. Call `migrator.Migrate1to2(ctx)`.
4. Observe it returns `ErrInsufficientBonusPool` wrapped as `"exit vested accounts positions: force-exit position N: claim rewards for position N: ..."`.
5. The upgrade handler receives this error, `RunMigrations` returns it, and the chain halts at the upgrade height.

The existing test `TestProcessEvents_InsufficientPool_Error` already confirms `processEventsAndClaimBonus` returns `ErrInsufficientBonusPool` when the pool is empty: [11](#0-10)

### Citations

**File:** x/tieredrewards/module.go (L105-108)
```go
	migrator := keeper.NewMigrator(am.keeper)
	if err := cfg.RegisterMigration(types.ModuleName, 1, migrator.Migrate1to2); err != nil {
		panic(fmt.Errorf("failed to register tieredrewards migration v1->v2: %w", err))
	}
```

**File:** x/tieredrewards/keeper/migrations.go (L17-18)
```go
func (m Migrator) Migrate1to2(ctx sdk.Context) error {
	return v2.Migrate(ctx, m.keeper.Positions, m.keeper.accountKeeper, m.keeper)
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L37-39)
```go
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-99)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-232)
```go
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

**File:** x/tieredrewards/keeper/slash.go (L54-63)
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
```

**File:** integration_tests/test_upgrade_v8.py (L124-126)
```python
    # Fund the rewards pool
    rsp = fund_pool(cluster, "signer1", f"50000000{DENOM}")
    assert rsp["code"] == 0, f"fund_pool failed: {rsp.get('raw_log', rsp)}"
```

**File:** x/tieredrewards/keeper/claim_rewards_test.go (L905-918)
```go
// TestProcessEvents_InsufficientPool_Error verifies that claiming without
// a funded pool returns ErrInsufficientBonusPool.
func (s *KeeperSuite) TestProcessEvents_InsufficientPool_Error() {
	lockAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	pos := s.setupNewTierPosition(lockAmount, false)

	// Advance time so bonus would be non-zero. Do NOT fund the pool.
	s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(30 * 24 * time.Hour))

	_, err := s.keeper.ProcessEventsAndClaimBonus(s.ctx, &pos)
	s.Require().Error(err, "should fail when bonus pool is insufficient")
	s.Require().ErrorContains(err, "insufficient bonus pool",
		"error should mention insufficient bonus pool")
}
```
