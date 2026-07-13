### Title
Insufficient Bonus Pool Blocks All Position-Management Operations, Locking User Delegations — (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

Every position-management message handler in the tiered-rewards module (`TierUndelegate`, `TierRedelegate`, `AddToTierPosition`, `ClearPosition`, `ExitTierWithDelegation`) unconditionally calls `claimRewards()` before executing its primary delegation operation. `claimRewards()` internally calls `processEventsAndClaimBonus()`, which calls `sufficientBonusPoolBalance()`. If the module's `rewards_pool` account cannot cover the accrued bonus for the position, the entire transaction reverts — including the delegation management step. A user whose primary intent is to exit, redelegate, or undelegate is blocked from doing so solely because the bonus pool is underfunded, even though the bonus pool is irrelevant to the delegation transfer itself.

---

### Finding Description

The call chain is:

```
MsgTierUndelegate / MsgTierRedelegate / MsgAddToTierPosition /
MsgClearPosition / MsgExitTierWithDelegation
  └─ claimRewards()                          [claim_rewards.go:87]
       └─ processEventsAndClaimBonus()        [claim_rewards.go:142]
            └─ sufficientBonusPoolBalance()   [bonus_rewards.go:48]
                 └─ returns ErrInsufficientBonusPool if pool < bonus
```

In `msg_server.go`, every mutating handler that touches a delegated position calls `claimRewards` before performing its core work:

- `TierUndelegate` — line 166 [1](#0-0) 
- `TierRedelegate` — line 229 [2](#0-1) 
- `AddToTierPosition` — line 314 [3](#0-2) 
- `ClearPosition` — line 406 [4](#0-3) 
- `ExitTierWithDelegation` — line 532 [5](#0-4) 

Inside `processEventsAndClaimBonus`, after computing `totalBonus`, the check is:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [6](#0-5) 

`sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` whenever the pool balance is less than the owed bonus:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
}
``` [7](#0-6) 

Because the error propagates atomically up through `claimRewards` and then through the message handler, the delegation management step (unbond, redelegate, transfer) is never reached. The position remains locked in the module.

The `ForceFullExitWithDelegation` migration helper in `force_exit.go` has the same flaw — it also calls `claimRewards` unconditionally at line 37 and would fail for the same reason. [8](#0-7) 

---

### Impact Explanation

When the `rewards_pool` module account is empty or holds less than the accrued bonus for a given position, the position owner cannot:

1. Undelegate their staked tokens (`MsgTierUndelegate`)
2. Redelegate to a different validator (`MsgTierRedelegate`)
3. Exit the tier and recover their delegation (`MsgExitTierWithDelegation`)
4. Clear a triggered exit to re-enter bonded state (`MsgClearPosition`)
5. Add more stake to the position (`MsgAddToTierPosition`)

The user's staked tokens are locked inside the tiered-rewards module's per-position delegator account with no user-accessible escape path. The only resolution is for the pool to be replenished by a governance or admin action. The corrupted invariant is: **a user's right to exit or manage their own delegation is contingent on the solvency of a module-level reward pool that is entirely outside the user's control.**

---

### Likelihood Explanation

The bonus pool is funded externally (e.g., via `MsgFundRewardsPool` or equivalent governance action). Under normal operation, as positions accrue bonus over time and claim it, the pool balance decreases. If the pool is not continuously topped up, it will eventually be insufficient to cover the accrued bonus of long-running positions. This is a realistic steady-state condition, not a theoretical edge case. Any position that has been delegated for a long time with a high `BonusApy` tier will accumulate a large bonus, and if the pool balance falls below that amount, the position is permanently stuck until governance acts.

---

### Recommendation

Apply the same fix class as the original report: decouple the bonus pool check from position-management operations. Concretely, in `processEventsAndClaimBonus`, when called from a non-claim context (undelegate, redelegate, exit), either:

1. **Skip bonus payment gracefully** — if `sufficientBonusPoolBalance` fails, advance the bonus accrual checkpoint without paying, and return zero bonus instead of an error. The owed bonus is preserved implicitly because `LastBonusAccrual` is only advanced after payment.
2. **Separate base and bonus settlement** — allow position-management handlers to call only `claimBaseRewards` (which has no pool dependency) and defer bonus settlement to an explicit `MsgClaimTierRewards` call.

Either approach ensures that a user's ability to exit or manage their delegation is never gated on the solvency of the bonus pool.

---

### Proof of Concept

1. User creates a tiered position via `MsgLockTier` with a high-`BonusApy` tier and delegates to a validator.
2. Time passes; the position accrues a large bonus (e.g., `shares × tokensPerShare × bonusApy × duration / SecondsPerYear` exceeds the current pool balance).
3. The bonus pool is not replenished (pool balance = 0 or < accrued bonus).
4. User triggers exit via `MsgTriggerExitFromTier` (succeeds — no reward claim here).
5. Exit lock duration elapses.
6. User submits `MsgExitTierWithDelegation` or `MsgTierUndelegate`.
7. Handler calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → returns `ErrInsufficientBonusPool`.
8. Transaction reverts. The delegation transfer never executes. User's staked tokens remain locked in the position's delegator address with no user-accessible exit path. [9](#0-8) [10](#0-9) [11](#0-10)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L166-169)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
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

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```
