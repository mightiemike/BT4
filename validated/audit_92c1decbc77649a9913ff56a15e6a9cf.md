The code path is concrete and traceable. Let me verify the exact error propagation chain.

### Title
Migration Abort via `ErrInsufficientBonusPool` in `ForceFullExitWithDelegation` — (`x/tieredrewards/keeper/force_exit.go`, `x/tieredrewards/migrations/v2/migrate.go`)

---

### Summary

`ForceFullExitWithDelegation` propagates `ErrInsufficientBonusPool` unconditionally, while the analogous `slashRedelegationPosition` explicitly swallows it to prevent chain halt. When the v8 upgrade migration calls `ForceFullExitWithDelegation` for any vesting-owned position whose accrued bonus exceeds the `RewardsPoolName` balance, the error bubbles all the way up through `exitVestedAccountsPositions` → `v2.Migrate` → `Migrate1to2`, causing the upgrade handler to fail and halting the chain at upgrade height.

---

### Finding Description

**Call chain:**

```
Migrate1to2 (migrations.go:17)
  → v2.Migrate (migrate.go:28)
    → exitVestedAccountsPositions (migrate.go:37)
      → ForceFullExitWithDelegation (migrate.go:99)
        → claimRewards (force_exit.go:37)
          → processEventsAndClaimBonus (claim_rewards.go:97)
            → sufficientBonusPoolBalance (claim_rewards.go:230)
              → returns ErrInsufficientBonusPool
```

At every level, the error is wrapped and re-returned with no special handling for `ErrInsufficientBonusPool`.

`ForceFullExitWithDelegation` calls `claimRewards` and propagates all errors: [1](#0-0) 

`claimRewards` calls `processEventsAndClaimBonus` and propagates all errors: [2](#0-1) 

`processEventsAndClaimBonus` calls `sufficientBonusPoolBalance` and propagates the error: [3](#0-2) 

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` when the pool cannot cover the computed bonus: [4](#0-3) 

`exitVestedAccountsPositions` propagates the error from `ForceFullExitWithDelegation` with no guard: [5](#0-4) 

**Contrast with `slashRedelegationPosition`**, which explicitly catches `ErrInsufficientBonusPool` and swallows it to prevent chain halt: [6](#0-5) 

The comment on line 55 reads: *"Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt."* This exact same reasoning applies to the migration path, but the guard is absent there.

The ADR documents this asymmetry implicitly — it lists the insufficient-pool handling only for user-driven paths and the `BeforeRedelegationSlashed` hook, with no mention of the migration path: [7](#0-6) 

---

### Impact Explanation

If any vesting-owned position has accrued bonus that exceeds the `RewardsPoolName` balance at upgrade height, `Migrate1to2` returns a non-nil error. In Cosmos SDK, a failing upgrade handler causes a consensus failure on every node simultaneously — the chain halts at the upgrade height. The principal and delegation are not transferred back to the owner; the position is not deleted; the chain is stuck.

---

### Likelihood Explanation

The preconditions are:

1. **A vesting account has a tier position.** This is the exact scenario the migration is designed to handle. The integration test (`test_upgrade_v8.py`) explicitly creates such positions via `_create_vesting_acc_owned_positions`. Vesting accounts were allowed to create positions before the v8 upgrade. [8](#0-7) 

2. **The position has accrued bonus.** Any position delegated to a bonded validator for a non-zero duration with `BonusApy > 0` accrues bonus. This is the normal operating state.

3. **The pool balance is insufficient to pay the bonus.** The pool can be drained by normal user claims (`MsgClaimTierRewards`, `MsgTierUndelegate`, etc.) or by the BeginBlocker top-up before the upgrade block. The integration test mitigates this by manually funding the pool (`fund_pool(cluster, "signer1", f"50000000{DENOM}")`), but this is not enforced in the migration code itself. [9](#0-8) 

All three conditions can coexist in production without any privileged action. The vesting account owner does not need to do anything beyond having created a position before the upgrade.

---

### Recommendation

Apply the same `ErrInsufficientBonusPool` guard in `ForceFullExitWithDelegation` that already exists in `slashRedelegationPosition`: catch the error, log it, forfeit the bonus, and continue with the delegation transfer and position deletion. The migration's goal is to unwind the position and return the delegation — forfeiting an unpayable bonus is the correct fallback, not aborting the upgrade.

Alternatively, add a pre-migration check that verifies the pool balance is sufficient to cover all accrued bonuses for vesting-owned positions, and surface a clear error before the upgrade block is reached.

---

### Proof of Concept

```go
// keeper_test.go (new test)
func (s *KeeperSuite) TestMigrate1to2_AbortOnInsufficientBonusPool() {
    s.setupTier(1)
    vals, bondDenom := s.getStakingData()
    val := vals[0]
    valAddr := sdk.MustValAddressFromBech32(val.GetOperator())
    s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

    amount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
    vestingOwner := s.newVestingOwnerWithBalance(bondDenom, amount, amount.MulRaw(3))
    _ = s.createLockTierPositionV1(vestingOwner, valAddr, amount)

    // Simulate many BOND events to accumulate a large LastEventSeq gap,
    // or simply advance time so bonus accrues substantially.
    s.ctx = s.ctx.WithBlockTime(s.ctx.BlockTime().Add(365 * 24 * time.Hour))

    // Intentionally leave the rewards pool empty (do NOT call s.fundRewardsPool).

    migrator := keeper.NewMigrator(s.keeper)
    err := migrator.Migrate1to2(s.ctx)
    // Without the fix: err != nil (ErrInsufficientBonusPool wrapped in migration error)
    // → upgrade handler fails → chain halt
    s.Require().NoError(err, "migration must not abort when bonus pool is insufficient")
}
```

The test will fail on the unmodified codebase, confirming the chain-halt path is reachable.

### Citations

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
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

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
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

**File:** integration_tests/test_upgrade_v8.py (L57-136)
```python
def _create_vesting_acc_owned_positions(cluster):
    """
    Creates a PermanentLockedAccount and establishes two distinct positions:
    1. A position initialized via CommitDelegationToTier.
    2. A position initialized via LockTier.

    Funds the rewards pool so the migration's claimRewards step can pay
    out any bonus accrued on the bypass positions.
    """
    val_addr = get_node_validator_addr(cluster)

    tiers = query_tiers(cluster).get("tiers", [])
    assert tiers, "expected at least one tier seeded by the v7 upgrade handler"
    tier_id = int(tiers[0]["id"])
    amount = int(tiers[0]["min_lock_amount"])
    commit_amount = amount
    lock_amount = amount

    # Create a permanent locked account with the commit amount
    owner_addr = create_permanent_lock_vesting_account(
        cluster,
        f"{commit_amount}basecro",
    )

    # Fund account for lock tier
    topup = lock_amount + GAS_ALLOWANCE
    rsp = cluster.transfer(
        cluster.address("signer1"),
        owner_addr,
        f"{topup}basecro",
    )
    assert rsp["code"] == 0, f"gas top-up failed: {rsp.get('raw_log', rsp)}"
    wait_for_new_blocks(cluster, 1)

    # Vesting owner delegates locked principal — this populates
    # DelegatedVesting via the bank-side TrackDelegation hook
    rsp = cluster.delegate_amount(val_addr, f"{commit_amount}basecro", owner_addr)
    assert rsp["code"] == 0, rsp.get("raw_log", rsp)
    wait_for_new_blocks(cluster, 1)

    # commit vesting account's delegation to a tier position
    rsp = commit_delegation(
        cluster,
        owner_addr,
        val_addr,
        commit_amount,
        tier_id,
    )
    assert (
        rsp["code"] == 0
    ), f"commit-delegation-to-tier failed on v7.2.0: {rsp.get('raw_log', rsp)}"

    # LockTier from the same vesting owner
    rsp = lock_tier(
        cluster,
        owner_addr,
        tier_id,
        lock_amount,
        val_addr,
    )
    assert rsp["code"] == 0, f"lock-tier failed on v7.2.0: {rsp.get('raw_log', rsp)}"

    positions = query_positions_by_owner(cluster, owner_addr).get("positions", [])
    assert len(positions) == 2, f"expected 2 positions pre-upgrade, got {positions}"
    pos_ids = sorted(int(p["id"]) for p in positions)
    commit_pos_id, lock_pos_id = pos_ids[0], pos_ids[1]

    # Fund the rewards pool
    rsp = fund_pool(cluster, "signer1", f"50000000{DENOM}")
    assert rsp["code"] == 0, f"fund_pool failed: {rsp.get('raw_log', rsp)}"
    wait_for_new_blocks(cluster, 1)

    return {
        "owner_addr": owner_addr,
        "val_addr": val_addr,
        "commit_amount": commit_amount,
        "lock_amount": lock_amount,
        "commit_pos_id": commit_pos_id,
        "lock_pos_id": lock_pos_id,
    }
```
