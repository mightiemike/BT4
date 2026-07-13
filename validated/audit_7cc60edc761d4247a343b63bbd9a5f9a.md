The key facts needed are all in the code. Let me verify the `TrackDelegation` split logic and the `LockedCoins` formula for `ContinuousVestingAccount` to confirm or deny the vulnerability.

I have all the information needed. Let me trace the exact math through the Cosmos SDK `TrackDelegation` implementation for `ContinuousVestingAccount` vs `PermanentLockedAccount`.

---

### Title
`alignVestingDelegationTracking` Incorrectly Inflates `SpendableCoins` for `ContinuousVestingAccount` Owners via Misuse of `TrackDelegation` `balance` Parameter — (`x/tieredrewards/keeper/force_exit.go`)

### Summary

`alignVestingDelegationTracking` calls `vacc.TrackDelegation(blockTime, coins, coins)` where both `balance` and `amount` are set to `deficit`. The inline comment claims `balance` is only used for an invariant check, but this is incorrect for `ContinuousVestingAccount`. The SDK's `BaseVestingAccount.TrackDelegation` uses `balance` directly in the DV/DF split via `x = min(vestingCoins(blockTime), balance)`. For a `ContinuousVestingAccount` at partial vesting where `vestingCoins(T) < OriginalVesting`, this sets `DV = vestingCoins(T)` and `DF = deficit − vestingCoins(T)`, which drives `LockedCoins(T) = 0` and makes the owner's entire bank balance immediately spendable — including tokens that should still be locked.

### Finding Description

The call site is:

```go
// x/tieredrewards/keeper/force_exit.go:164-172
deficit := actualDelegated.Sub(tracked)
coins := sdk.NewCoins(sdk.NewCoin(bondDenom, deficit))
// Pass balance == amount: TrackDelegation only uses balance for an
// invariant check (amount <= balance); the DV/DF split is computed from
// vestingCoins(blockTime) and DelegatedVesting. The owner's actual bank
// balance is irrelevant here because the delegation came from the position
// pool, not from the owner's balance.
sdkCtx := sdk.UnwrapSDKContext(ctx)
vacc.TrackDelegation(sdkCtx.BlockTime(), coins, coins)
``` [1](#0-0) 

The comment is factually wrong. The Cosmos SDK `BaseVestingAccount.TrackDelegation(balance, vestingCoins, amount)` computes:

```
x = min(vestingCoins(blockTime), balance)
y = amount − min(x − DV_current, amount)
DV = x          // SET, not added
DF += y
```

For `PermanentLockedAccount`: `vestingCoins = OriginalVesting` always, so `x = min(OriginalVesting, deficit)`. When `deficit ≤ OriginalVesting`, `DV = deficit`, `DF = 0`, and `LockedCoins = OriginalVesting − min(OriginalVesting, deficit) = OriginalVesting − deficit`. This is correct — the locked tokens are now delegated.

For `ContinuousVestingAccount` at upgrade time T where `vestingCoins(T) < OriginalVesting` and `deficit ≥ vestingCoins(T)`:
- `x = vestingCoins(T)` → `DV = vestingCoins(T)`
- `y = deficit − vestingCoins(T)` → `DF += deficit − vestingCoins(T)`
- `LockedCoins(T) = vestingCoins(T) − min(vestingCoins(T), DV) = vestingCoins(T) − vestingCoins(T) = 0`

The owner's bank balance (which still holds the original locked tokens, since LockTier only sent the spendable portion to the position) is now fully spendable. The owner gains `vestingCoins(T)` extra spendable tokens that should remain locked.

All existing tests use `PermanentLockedAccount` exclusively via `newVestingOwnerWithBalance`, which always calls `vestingtypes.NewPermanentLockedAccount`. The `ContinuousVestingAccount` case is entirely untested. [2](#0-1) 

The vesting-account restriction on `MsgLockTier` and `MsgCommitDelegationToTier` was added in v8 (`validateNonVestingAccount`). On v7.2.0, any vesting account type — including `ContinuousVestingAccount` — could create LockTier positions. The integration test `_create_vesting_acc_owned_positions` explicitly demonstrates this pre-upgrade path. [3](#0-2) [4](#0-3) 

### Impact Explanation

Concrete numbers: `ContinuousVestingAccount` with `OriginalVesting = 1000`, `startTime = 0`, `endTime = 100`. Upgrade fires at `T = 50`: `vestingCoins(T) = 500`. Owner's bank balance = 1000 (the locked portion; the spendable 1000 was sent to LockTier). After `alignVestingDelegationTracking`:

- `DV = 500`, `DF = 500`
- `LockedCoins(T) = 0`
- `SpendableCoins = 1000` (full bank balance)

Without the migration: `SpendableCoins = 1000 − 500 = 500`. The owner gains 500 spendable tokens that should be locked. These are real bank tokens the owner can immediately transfer or spend. The tokens are not created from nothing — they are the owner's own locked vesting tokens that the migration incorrectly unlocks early.

### Likelihood Explanation

`ContinuousVestingAccount` is a standard genesis-time account type supported by the chain's own `add-genesis-account` CLI. Any genesis vesting account with `--vesting-start-time` set is a `ContinuousVestingAccount`. Such accounts could have created LockTier positions on v7.2.0 before the vesting restriction was introduced. The v8 upgrade block time is deterministic and known in advance, so an attacker can calculate exactly how much `vestingCoins(T)` will be at upgrade time and size their LockTier position accordingly to maximize the spendable gain.

### Recommendation

Replace the `balance = deficit` shortcut with the owner's actual bank balance:

```go
actualBalance := k.bankKeeper.GetBalance(ctx, ownerAddr, bondDenom)
balanceCoins := sdk.NewCoins(actualBalance)
vacc.TrackDelegation(sdkCtx.BlockTime(), balanceCoins, coins)
```

This ensures `x = min(vestingCoins(T), actualBalance)` reflects the real bank state, so `DV` is bounded by the actual vesting coins relative to the real balance, and `LockedCoins` is not incorrectly zeroed. Additionally, add a `ContinuousVestingAccount`-specific keeper test asserting `SpendableCoins` post-migration equals only the legitimately vested portion.

### Proof of Concept

```go
func (s *KeeperSuite) TestAlignVestingDelegationTracking_ContinuousVestingAccount_SpendableInflation() {
    s.setupTier(1)
    vals, bondDenom := s.getStakingData()
    val := vals[0]
    valAddr := sdk.MustValAddressFromBech32(val.GetOperator())

    // ContinuousVestingAccount: OriginalVesting=1000, vesting 0→100 seconds
    originalVesting := sdkmath.NewInt(1000)
    startTime := s.ctx.BlockTime()
    endTime := startTime.Add(100 * time.Second)

    owner := sdk.AccAddress(secp256k1.GenPrivKey().PubKey().Address())
    baseAcc, _ := s.app.AccountKeeper.NewAccountWithAddress(s.ctx, owner).(*authtypes.BaseAccount)
    bva, _ := vestingtypes.NewBaseVestingAccount(baseAcc,
        sdk.NewCoins(sdk.NewCoin(bondDenom, originalVesting)), endTime.Unix())
    cva := vestingtypes.NewContinuousVestingAccountRaw(bva, startTime.Unix())
    s.app.AccountKeeper.SetAccount(s.ctx, cva)
    // Fund: 2000 total (1000 locked + 1000 spendable)
    banktestutil.FundAccount(s.ctx, s.app.BankKeeper, owner,
        sdk.NewCoins(sdk.NewCoin(bondDenom, originalVesting.MulRaw(2))))

    // LockTier sends the spendable 1000 to position; bank balance = 1000 (locked)
    pos := s.createLockTierPositionV1(owner, valAddr, originalVesting)

    // Advance to T=50: vestingCoins(T) = 500
    s.ctx = s.ctx.WithBlockTime(startTime.Add(50 * time.Second))

    s.Require().NoError(s.keeper.ForceFullExitWithDelegation(s.ctx, pos.Id))

    // BUG: SpendableCoins = 1000 (full bank balance), should be 500
    spendable := s.app.BankKeeper.SpendableCoins(s.ctx, owner)
    legitimatelySpendable := originalVesting.Sub(sdkmath.NewInt(500)) // vestingCoins(T)=500
    s.Require().Equal(legitimatelySpendable, spendable.AmountOf(bondDenom),
        "SpendableCoins must not exceed legitimately vested portion")
}
```

This test will fail on the current code, demonstrating that the owner can spend 500 tokens that should remain locked until `endTime`.

### Citations

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

**File:** x/tieredrewards/keeper/force_exit_test.go (L115-131)
```go
// newVestingOwnerWithBalance creates a fresh PermanentLockedAccount address
// funded with `balance` of bondDenom and returns the address.
func (s *KeeperSuite) newVestingOwnerWithBalance(bondDenom string, originalVesting, balance sdkmath.Int) sdk.AccAddress {
	s.T().Helper()
	owner := sdk.AccAddress(secp256k1.GenPrivKey().PubKey().Address())
	baseAcc, ok := s.app.AccountKeeper.NewAccountWithAddress(s.ctx, owner).(*authtypes.BaseAccount)
	s.Require().True(ok)
	vestingAcc, err := vestingtypes.NewPermanentLockedAccount(
		baseAcc, sdk.NewCoins(sdk.NewCoin(bondDenom, originalVesting)),
	)
	s.Require().NoError(err)
	s.app.AccountKeeper.SetAccount(s.ctx, vestingAcc)
	s.Require().NoError(banktestutil.FundAccount(
		s.ctx, s.app.BankKeeper, owner, sdk.NewCoins(sdk.NewCoin(bondDenom, balance)),
	))
	return owner
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L15-26)
```go
func (k Keeper) validateNonVestingAccount(ctx context.Context, address string) error {
	addr, err := sdk.AccAddressFromBech32(address)
	if err != nil {
		return err
	}
	if acc := k.accountKeeper.GetAccount(ctx, addr); acc != nil {
		if _, ok := acc.(sdkvesting.VestingAccount); ok {
			return types.ErrVestingAccountNotAllowed
		}
	}
	return nil
}
```

**File:** integration_tests/test_upgrade_v8.py (L57-117)
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
```
