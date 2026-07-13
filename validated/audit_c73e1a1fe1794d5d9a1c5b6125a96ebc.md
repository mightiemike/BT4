### Title
Vesting Lock Bypass via `alignVestingDelegationTracking` Inflating `DelegatedVesting` with Position-Pool Coins — (`x/tieredrewards/keeper/force_exit.go`)

---

### Summary

`alignVestingDelegationTracking` calls `vacc.TrackDelegation(blockTime, coins, coins)` with `balance == amount == deficit`. For a `PermanentLockedAccount` owner whose LockTier position was funded from spendable coins, this call incorrectly inflates `DelegatedVesting` up to `OriginalVesting`. Because `lockedCoins = max(OriginalVesting − DelegatedVesting, 0)`, setting `DelegatedVesting = OriginalVesting` zeroes out `lockedCoins`, making the owner's permanently-locked bank balance fully spendable after the v8 upgrade migration.

---

### Finding Description

**Entry path:** The v8 upgrade handler calls `exitVestedAccountsPositions` in `x/tieredrewards/migrations/v2/migrate.go`, which iterates every position whose owner is a `VestingAccount` and calls `ForceFullExitWithDelegation` for each. [1](#0-0) 

`ForceFullExitWithDelegation` calls `transferDelegationFromPosition` (with `subtractAccount=false`, skipping the bank-side `TrackDelegation` hook) and then calls `alignVestingDelegationTracking`. [2](#0-1) 

Inside `alignVestingDelegationTracking`, the deficit is computed as `actualDelegated − (DV + DF)` and `TrackDelegation` is called with `balance == amount == deficit`: [3](#0-2) 

The comment acknowledges the bypass: *"TrackDelegation only uses balance for an invariant check (amount <= balance)"*. This is correct — the SDK's `BaseVestingAccount.TrackDelegation` panics only if `amount > balance`. By passing `balance = amount`, the check is trivially satisfied regardless of the owner's real bank balance.

**The DV/DF split is then computed as:**
```
x = min(max(vestingCoins(blockTime) − DV, 0), amount)
y = amount − x
DV += x,  DF += y
```

For a `PermanentLockedAccount`, `vestingCoins(blockTime) = OriginalVesting` always. So if `DV = 0` and `deficit ≥ OriginalVesting`, then `x = OriginalVesting`, and `DV` is set to `OriginalVesting`.

**The problem:** In the `LockTier` origin scenario, the delegation returned to the owner came from the owner's *spendable* coins (sent via `Bank.SendCoins` to the position delegator), not from the owner's locked vesting coins. Calling `TrackDelegation` with this amount incorrectly records the locked coins as if they were delegated. The SDK's `SpendableCoins` then computes:

```
lockedCoins = max(OriginalVesting − DelegatedVesting, 0) = max(X − X, 0) = 0
spendable   = bank_balance − 0 = bank_balance
```

The owner's permanently-locked bank balance (which was never moved) is now fully spendable.

---

### Impact Explanation

A `PermanentLockedAccount` owner who, before the v8 upgrade:
1. Had `OriginalVesting = X` (permanently locked)
2. Accumulated spendable coins `Y ≥ X` (e.g., staking rewards)
3. Used `MsgLockTier` to lock `Y` spendable coins into a position

After the v8 upgrade migration runs `ForceFullExitWithDelegation`:
- `DelegatedVesting` is set to `X = OriginalVesting`
- `lockedCoins = 0`
- The owner's `X` locked bank coins become fully spendable
- The owner also holds a `Y`-token delegation (returnable via undelegate)

Net effect: the owner can spend `X` coins that should be permanently locked, constituting unauthorized asset movement of locked vesting tokens.

---

### Likelihood Explanation

The attack requires a `PermanentLockedAccount` with accumulated spendable coins ≥ `OriginalVesting` and a `LockTier` position created before the v8 upgrade. `PermanentLockedAccount` is used for team/investor allocations; staking rewards accumulate as free coins over time. The setup is realistic for any long-running vesting account that participated in the tiered rewards module before v8.

---

### Recommendation

`alignVestingDelegationTracking` must not use `TrackDelegation` to account for delegations that originated from the position pool (spendable coins). The correct fix is to increase only `DelegatedFree` by the deficit (since the returning delegation represents spendable coins, not vesting coins), bypassing `TrackDelegation` entirely and directly mutating `DelegatedFree` on the `BaseVestingAccount`. Alternatively, the function should pass the owner's real spendable bank balance as the `balance` argument, which would cause `TrackDelegation` to panic (correctly) when the deficit exceeds the spendable balance, surfacing the accounting mismatch.

---

### Proof of Concept

State before v8 upgrade:
- `PermanentLockedAccount`: `OriginalVesting = 1_000_000`, bank balance = `2_000_000` (1M locked + 1M spendable)
- `LockTier` sends 1M spendable to position delegator; position delegates 1M
- Owner bank balance = 1M (locked), DV = 0, DF = 0

v8 upgrade runs `ForceFullExitWithDelegation`:
- `transferDelegationFromPosition` returns 1M delegation to owner (subtractAccount=false, DV/DF unchanged)
- `actualDelegated = 1M`, `tracked = 0`, `deficit = 1M`
- `TrackDelegation(blockTime, {1M}, {1M})` → `x = min(1M, 1M) = 1M`, `y = 0`
- `DelegatedVesting = 1M = OriginalVesting`

Post-upgrade state:
- `lockedCoins = max(1M − 1M, 0) = 0`
- `SpendableCoins = 1M` (the originally-locked bank balance)
- Owner can send/spend the 1M locked coins

The existing test `TestForceFullExitWithDelegation_VestingOwner_LockOrigin` asserts `DV == OriginalVesting` but never asserts `SpendableCoins == 0` for the remaining bank balance, missing this invariant violation. [4](#0-3)

### Citations

**File:** x/tieredrewards/migrations/v2/migrate.go (L97-101)
```go
	for _, posID := range toExit {
		sdkCtx.Logger().Info("v8 migration: force-exit vesting-owned position", "position_id", posID)
		if err := pk.ForceFullExitWithDelegation(ctx, posID); err != nil {
			return fmt.Errorf("force-exit position %d: %w", posID, err)
		}
```

**File:** x/tieredrewards/keeper/force_exit.go (L62-77)
```go
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
```

**File:** x/tieredrewards/keeper/force_exit.go (L163-172)
```go
	deficit := actualDelegated.Sub(tracked)
	coins := sdk.NewCoins(sdk.NewCoin(bondDenom, deficit))
	// Pass balance == amount: TrackDelegation only uses balance for an
	// invariant check (amount <= balance); the DV/DF split is computed from
	// vestingCoins(blockTime) and DelegatedVesting. The owner's actual bank
	// balance is irrelevant here because the delegation came from the position
	// pool, not from the owner's balance.
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	vacc.TrackDelegation(sdkCtx.BlockTime(), coins, coins)
	k.accountKeeper.SetAccount(ctx, vacc)
```

**File:** x/tieredrewards/keeper/force_exit_test.go (L204-247)
```go
func (s *KeeperSuite) TestForceFullExitWithDelegation_VestingOwner_LockOrigin() {
	s.setupTier(1)
	vals, bondDenom := s.getStakingData()
	val := vals[0]
	valAddr := sdk.MustValAddressFromBech32(val.GetOperator())
	s.setValidatorCommission(valAddr, sdkmath.LegacyZeroDec())

	lockedAmount := sdkmath.NewInt(sdk.DefaultPowerReduction.Int64())
	// OriginalVesting equals the locked amount; the account is funded with
	// 2*lockedAmount so that lockedAmount of it is spendable (balance −
	// LockedCoins). LockTier consumes the spendable portion via bank send,
	// without touching DelegatedVesting/DelegatedFree.
	owner := s.newVestingOwnerWithBalance(bondDenom, lockedAmount, lockedAmount.MulRaw(2))

	pos := s.createLockTierPositionV1(owner, valAddr, lockedAmount)

	// Pre-migration: DV/DF are zero (LockTier didn't touch them); owner has
	// no on-chain delegation.
	s.Require().True(s.delegatedVesting(owner).AmountOf(bondDenom).IsZero(),
		"DV must be zero for a LockTier-origin position before force exit")
	s.Require().True(s.delegatedFree(owner).AmountOf(bondDenom).IsZero(),
		"DF must be zero for a LockTier-origin position before force exit")
	s.Require().True(s.totalDelegated(owner).IsZero(),
		"owner has no on-chain delegation pre force-exit; position holds it")

	s.advanceForRewards(valAddr, bondDenom)

	s.Require().NoError(s.keeper.ForceFullExitWithDelegation(s.ctx, pos.Id))

	_, err := s.keeper.GetPosition(s.ctx, pos.Id)
	s.Require().Error(err)
	ownerDel := s.totalDelegated(owner)
	s.Require().Equal(lockedAmount, ownerDel,
		"owner must hold the returned delegation post force-exit")

	// Alignment must have topped up DV+DF by lockedAmount; otherwise a later
	// normal Undelegate would underflow vesting accounting.
	s.Require().Equal(lockedAmount, s.delegatedVesting(owner).AmountOf(bondDenom),
		"DV must saturate at OriginalVesting (= lockedAmount)")
	s.Require().True(s.delegatedFree(owner).AmountOf(bondDenom).IsZero(),
		"DF must be zero — the deficit fits entirely within OriginalVesting")
	s.Require().Equal(ownerDel, s.trackedTotal(owner, bondDenom),
		"alignment must satisfy DV + DF == Σ delegations for LockTier-origin positions")
}
```
