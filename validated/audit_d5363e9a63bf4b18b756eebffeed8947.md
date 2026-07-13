### Title
Base Rewards Permanently Locked in Position Delegator Account During v8 Migration Force-Exit — (`x/tieredrewards/keeper/force_exit.go`, `x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

The v2 migration (`Migrate1to2`) calls `ForceFullExitWithDelegation` for every vesting-account-owned position. Before doing so, it only backfills the `DelegatorAddress` field in the position record — it never calls `routeBaseRewardsToOwner` to set the distribution withdraw address for the position delegator. As a result, `claimBaseRewards` inside `ForceFullExitWithDelegation` calls `WithdrawDelegationRewards` on `posDelAddr`, and the Cosmos SDK distribution module sends the rewards to `posDelAddr` itself (the default when no withdraw address override exists). The position is then deleted. The rewards are permanently locked in `posDelAddr`, a module-derived address controlled by no private key.

---

### Finding Description

**Step 1 — Migration entry point**

`Migrator.Migrate1to2` calls `v2.Migrate`: [1](#0-0) 

`v2.Migrate` runs two steps in sequence: [2](#0-1) 

**Step 2 — `backfillDelegatorAddress` sets the field but never sets the withdraw address**

`backfillDelegatorAddress` iterates every position and writes `pos.DelegatorAddress = LegacyDelegatorAddress(pos.Id)`: [3](#0-2) 

`LegacyDelegatorAddress` produces a module-derived address: [4](#0-3) 

Critically, **no call to `routeBaseRewardsToOwner` (i.e., `SetWithdrawAddr`) is made anywhere in the migration**. The distribution module's withdraw address for `posDelAddr` is therefore never set to the owner.

**Step 3 — `exitVestedAccountsPositions` calls `ForceFullExitWithDelegation`** [5](#0-4) 

**Step 4 — `ForceFullExitWithDelegation` calls `claimRewards` before transferring the delegation** [6](#0-5) 

**Step 5 — `claimBaseRewards` assumes the withdraw address is already set**

The function's own comment states the assumption: [7](#0-6) 

It calls `WithdrawDelegationRewards` on `posDelAddr`: [8](#0-7) 

Because no withdraw address was ever set for `posDelAddr` (the migration skipped `routeBaseRewardsToOwner`), the Cosmos SDK distribution module defaults to sending rewards to `posDelAddr` itself — not to the owner.

**Step 6 — `deletePosition` removes the position record; rewards remain in `posDelAddr`** [9](#0-8) 

`deletePosition` calls `removeBaseRewardsRouting` → `DeleteDelegatorWithdrawAddr`. Since no withdraw address record was ever written for `posDelAddr`, this is a no-op. The position record is removed, but the base rewards already sent to `posDelAddr` remain there permanently. [10](#0-9) 

**Contrast with the normal creation path**

When a position is created normally, `createDelegatedPosition` always calls `routeBaseRewardsToOwner` before the position is persisted: [11](#0-10) 

`routeBaseRewardsToOwner` calls `SetWithdrawAddr`: [12](#0-11) 

The migration never performs this step for legacy positions.

---

### Impact Explanation

Base staking rewards accrued by legacy vesting-account-owned positions are sent to `posDelAddr` — a module-derived address (`authtypes.NewModuleAddress("tieredrewards/position/<id>")`) that has no corresponding private key. The rewards cannot be recovered. This is a permanent, irreversible loss of user funds triggered automatically during the v8 chain upgrade for every vesting account that held a legacy tiered-rewards position.

---

### Likelihood Explanation

The path is deterministic and automatic: the v8 upgrade handler calls `Migrate1to2`, which calls `ForceFullExitWithDelegation` for every vesting-account-owned position. No attacker action is required; the loss occurs unconditionally for all affected positions during the upgrade block. Any chain state with at least one vesting-account-owned legacy position triggers the bug.

---

### Recommendation

In `backfillDelegatorAddress` (or in a dedicated step before `exitVestedAccountsPositions`), call `routeBaseRewardsToOwner(ctx, posDelAddr, ownerAddr)` for every legacy position so that the distribution withdraw address is set before `ForceFullExitWithDelegation` is invoked. Alternatively, add the call at the top of `ForceFullExitWithDelegation` itself, guarded by a check that the withdraw address is not already set to the owner.

---

### Proof of Concept

```
1. Chain state before v8 upgrade:
   - Position ID=1, owner=vestingAccAddr, DelegatorAddress="" (legacy, field not yet introduced)
   - Staking delegation: posDelAddr (LegacyDelegatorAddress(1)) → validator, 1000 base rewards accrued
   - distribution.WithdrawAddress[posDelAddr] = NOT SET (routeBaseRewardsToOwner never called)

2. v8 upgrade triggers Migrate1to2 → v2.Migrate

3. backfillDelegatorAddress:
   - pos.DelegatorAddress = LegacyDelegatorAddress(1)  // field set, no SetWithdrawAddr called

4. exitVestedAccountsPositions:
   - owner is a VestingAccount → ForceFullExitWithDelegation(ctx, 1)

5. ForceFullExitWithDelegation → claimRewards → claimBaseRewards:
   - WithdrawDelegationRewards(ctx, posDelAddr, valAddr)
   - distribution module: no withdraw address set → sends 1000 rewards to posDelAddr
   - vestingAccAddr receives 0 base rewards

6. deletePosition:
   - removeBaseRewardsRouting(posDelAddr, vestingAccAddr) → no-op (no record existed)
   - position record removed

7. Final state:
   - posDelAddr holds 1000 base rewards
   - posDelAddr has no private key → funds permanently locked
   - vestingAccAddr lost all base rewards
```

### Citations

**File:** x/tieredrewards/keeper/migrations.go (L17-19)
```go
func (m Migrator) Migrate1to2(ctx sdk.Context) error {
	return v2.Migrate(ctx, m.keeper.Positions, m.keeper.accountKeeper, m.keeper)
}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L24-26)
```go
func LegacyDelegatorAddress(id uint64) string {
	return authtypes.NewModuleAddress(fmt.Sprintf("tieredrewards/position/%d", id)).String()
}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L28-41)
```go
func Migrate(
	ctx context.Context,
	positions collections.Map[uint64, types.Position],
	ak AccountKeeper,
	pk PositionForceExiter,
) error {
	if err := backfillDelegatorAddress(ctx, positions); err != nil {
		return fmt.Errorf("backfill delegator address: %w", err)
	}
	if err := exitVestedAccountsPositions(ctx, positions, ak, pk); err != nil {
		return fmt.Errorf("exit vested accounts positions: %w", err)
	}
	return nil
}
```

**File:** x/tieredrewards/migrations/v2/migrate.go (L57-65)
```go
	for _, kv := range kvs {
		pos := kv.Value
		pos.DelegatorAddress = LegacyDelegatorAddress(pos.Id)
		if _, err := sdk.AccAddressFromBech32(pos.DelegatorAddress); err != nil {
			return fmt.Errorf("backfill produced invalid delegator address for position %d: %w", pos.Id, err)
		}
		if err := positions.Set(ctx, pos.Id, pos); err != nil {
			return err
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

**File:** x/tieredrewards/keeper/force_exit.go (L37-45)
```go
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

**File:** x/tieredrewards/keeper/force_exit.go (L79-85)
```go
	if err := k.deletePosition(ctx, posState.Position, &ValidatorTransition{PreviousAddress: valAddr.String()}); err != nil {
		return fmt.Errorf("delete position %d: %w", posID, err)
	}
	logger.Info("force-exit: position deleted",
		"position_id", posID,
		"owner", posState.Owner,
	)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L15-18)
```go
// claimBaseRewards claims the outstanding base rewards held
// by the given position's delegation for a single validator.
// This assumes that the delegation withdrawAddress has been set to the position's owner address.
func (k Keeper) claimBaseRewards(ctx context.Context, pos types.PositionState) (sdk.Coins, error) {
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L26-33)
```go
	posDelAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}
	rewards, err := k.distributionKeeper.WithdrawDelegationRewards(ctx, posDelAddr, valAddr)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/position.go (L63-65)
```go
	if err := k.routeBaseRewardsToOwner(ctx, delAddr, ownerAddr); err != nil {
		return types.Position{}, err
	}
```

**File:** x/tieredrewards/keeper/position.go (L106-108)
```go
func (k Keeper) routeBaseRewardsToOwner(ctx context.Context, posDelAddr, ownerAddr sdk.AccAddress) error {
	return k.distributionKeeper.SetWithdrawAddr(ctx, posDelAddr, ownerAddr)
}
```

**File:** x/tieredrewards/keeper/position.go (L221-223)
```go
	if err := k.removeBaseRewardsRouting(ctx, delAddr, owner); err != nil {
		return err
	}
```
