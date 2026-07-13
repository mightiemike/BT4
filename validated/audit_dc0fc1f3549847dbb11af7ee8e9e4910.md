### Title
v8 Migration Halts if Vesting-Owned Tier Position Delegates to a Jailed Validator — (File: `x/tieredrewards/migrations/v2/migrate.go`)

### Summary
The v8 upgrade migration unconditionally calls `ForceFullExitWithDelegation` for every vesting-owned tier position. That function internally calls `transferDelegationFromPosition`, which hard-rejects non-bonded validators with `ErrValidatorNotBonded`. If any vesting-owned position is delegated to a jailed or unbonding validator at upgrade time, the migration returns an error, the upgrade handler fails, and the chain upgrade halts.

### Finding Description
`exitVestedAccountsPositions` iterates all positions owned by vesting accounts and calls `pk.ForceFullExitWithDelegation(ctx, posID)` for each one, propagating any error directly: [1](#0-0) 

`ForceFullExitWithDelegation` calls `transferDelegationFromPosition` and propagates its error: [2](#0-1) 

Inside `transferDelegationFromPosition`, there is an unconditional bonded-validator guard: [3](#0-2) 

This guard exists because the function performs an instant Unbond + re-Delegate (no unbonding queue), which the Cosmos SDK only permits on a bonded validator. The migration code never checks the validator's bonded status before invoking this path, so a jailed or unbonding validator causes the call to return `ErrValidatorNotBonded`.

The error propagates in full:

```
transferDelegationFromPosition
  → ForceFullExitWithDelegation (force_exit.go:62-64)
  → exitVestedAccountsPositions (migrate.go:99-101)
  → Migrate (migrate.go:37-39)
  → Migrate1to2 (migrations.go:17-18)
  → RunMigrations (upgrades.go:58-61)
  → upgrade handler returns error
  → chain upgrade halts
``` [4](#0-3) [5](#0-4) [6](#0-5) 

The ADR explicitly documents this limitation for user-facing messages ("Validator jailed/unbonding | … ExitTierWithDelegation fails") but the migration code does not account for it: [7](#0-6) 

### Impact Explanation
The v8 chain upgrade fails to execute. The upgrade block returns an error, halting the chain. All user funds in tier positions remain locked and inaccessible until a corrected upgrade plan is deployed. The corrupted invariant is the upgrade handler's expected successful completion: the `VersionMap` returned by `RunMigrations` is never committed, leaving module consensus versions in an inconsistent state.

### Likelihood Explanation
The v8 migration is specifically designed to handle vesting accounts that bypassed the vesting-account restriction added after v1 — such positions exist on mainnet by design. Validators are routinely jailed for downtime or double-signing. The combination of a vesting-owned position and a jailed validator at upgrade time is a realistic, non-adversarial scenario. No privileged access is required; normal chain operation (validator jailing) is sufficient to trigger it.

### Recommendation
In `ForceFullExitWithDelegation`, check whether the validator is bonded before calling `transferDelegationFromPosition`. If the validator is not bonded, fall back to the undelegate path (`stakingKeeper.Undelegate`) to start the standard unbonding period instead of the instant delegation-transfer path. This mirrors the existing user-facing fallback (`MsgTierUndelegate`) and avoids the `ErrValidatorNotBonded` failure during migration.

### Proof of Concept
1. Before the v8 upgrade block, a `PermanentLockedAccount` holds a tier position delegated to validator V (created in v1 before the vesting-account restriction was enforced).
2. Validator V is jailed for downtime before the upgrade block; its status transitions to `Unbonding`.
3. The upgrade block executes the v8 upgrade handler, which calls `RunMigrations`.
4. `Migrate1to2` → `exitVestedAccountsPositions` identifies the vesting-owned position and calls `ForceFullExitWithDelegation`.
5. `ForceFullExitWithDelegation` calls `transferDelegationFromPosition` with the jailed validator's address.
6. `transferDelegationFromPosition` evaluates `validator.IsBonded()` → `false` → returns `ErrValidatorNotBonded`.
7. The error propagates through the entire migration chain; the upgrade handler returns a non-nil error.
8. The chain upgrade fails; the chain halts at the upgrade height.

### Citations

**File:** x/tieredrewards/migrations/v2/migrate.go (L71-106)
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
}
```

**File:** x/tieredrewards/keeper/force_exit.go (L14-94)
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

	valAddr, err := sdk.ValAddressFromBech32(posState.Delegation.ValidatorAddress)
	if err != nil {
		return fmt.Errorf("parse validator address for position %d: %w", posID, err)
	}

	positionAmount, err := k.reconcileAmountFromShares(ctx, valAddr, posState.Delegation.Shares)
	if err != nil {
		return fmt.Errorf("reconcile amount for position %d: %w", posID, err)
	}
	logger.Info("force-exit: reconciled position amount",
		"position_id", posID,
		"amount", positionAmount.String(),
		"validator", valAddr.String(),
	)

	if _, _, _, err := k.transferDelegationFromPosition(ctx, posState, valAddr, positionAmount); err != nil {
		return fmt.Errorf("transfer delegation back to owner for position %d: %w", posID, err)
	}
	logger.Info("force-exit: delegation transferred back to owner",
		"position_id", posID,
		"owner", posState.Owner,
		"amount", positionAmount.String(),
	)

	ownerAddr, err := sdk.AccAddressFromBech32(posState.Owner)
	if err != nil {
		return fmt.Errorf("parse owner address for position %d: %w", posID, err)
	}
	if err := k.alignVestingDelegationTracking(ctx, ownerAddr); err != nil {
		return fmt.Errorf("align vesting delegation tracking for position %d: %w", posID, err)
	}

	if err := k.deletePosition(ctx, posState.Position, &ValidatorTransition{PreviousAddress: valAddr.String()}); err != nil {
		return fmt.Errorf("delete position %d: %w", posID, err)
	}
	logger.Info("force-exit: position deleted",
		"position_id", posID,
		"owner", posState.Owner,
	)

	logger.Info("force-exit: complete",
		"position_id", posID,
		"owner", posState.Owner,
		"amount", positionAmount.String(),
		"validator", valAddr.String(),
	)
	return nil
}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L132-134)
```go
	if !validator.IsBonded() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrValidatorNotBonded
	}
```

**File:** app/upgrades.go (L54-65)
```go
	app.UpgradeKeeper.SetUpgradeHandler(UpgradeV8PlanName, func(ctx context.Context, plan upgradetypes.Plan, fromVM module.VersionMap) (module.VersionMap, error) {
		sdkCtx := sdk.UnwrapSDKContext(ctx)

		sdkCtx.Logger().Info("v8: running module migrations...")
		m, err := app.ModuleManager.RunMigrations(ctx, app.configurator, fromVM)
		if err != nil {
			return map[string]uint64{}, err
		}

		sdkCtx.Logger().Info("v8: upgrade completed", "plan", plan.Name, "version_map", m)
		return m, nil
	})
```

**File:** doc/architecture/adr-006.md (L333-333)
```markdown
| Validator jailed/unbonding | Position stays valid. No base rewards while inactive. No bonus while not bonded. User can redelegate or undelegate (if exit elapsed). ExitTierWithDelegation fails.|
```
