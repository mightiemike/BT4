### Title
Mandatory Bonus Reward Settlement in Exit Paths Blocks Principal Token Withdrawal When Bonus Pool Is Depleted — (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

Both exit paths from a tier position — `MsgTierUndelegate` and `MsgExitTierWithDelegation` — unconditionally call `claimRewards` before executing any exit logic. `claimRewards` calls `processEventsAndClaimBonus`, which checks `sufficientBonusPoolBalance` and returns `ErrInsufficientBonusPool` if the pool cannot cover accrued bonus. Because this check is mandatory and non-bypassable, an empty or under-funded bonus pool blocks all users with accrued bonus from exiting their positions, locking their principal tokens in the module indefinitely.

---

### Finding Description

`MsgTierUndelegate` (the first step of the undelegate+withdraw exit path) calls `claimRewards` unconditionally at line 166 of `msg_server.go`:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
```

`MsgExitTierWithDelegation` (the instant delegation-transfer exit path) does the same at line 532:

```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
```

`claimRewards` in `claim_rewards.go` calls `processEventsAndClaimBonus`, which at line 230 checks pool solvency:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
```

`sufficientBonusPoolBalance` in `bonus_rewards.go` returns `ErrInsufficientBonusPool` if the pool balance is below the accrued bonus:

```go
if !poolBalance.IsAllGTE(bonus) {
    return errorsmod.Wrapf(types.ErrInsufficientBonusPool,
        "bonus: %s, pool balance: %s",
        bonus.String(), poolBalance.String())
}
```

The ADR explicitly documents this behavior: *"Pool empty (user-driven) | Message fails atomically. No state change. Retry after pool replenished."* and *"User-driven paths (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically."*

The bonus pool is drained by two mechanisms:
1. The `BeginBlocker` continuously transfers shortfall amounts from the pool to the distribution module every block when `TargetBaseRewardsRate > 0`.
2. Users claiming bonus rewards via `MsgClaimTierRewards`.

Once the pool is empty, every user whose position has accrued non-zero bonus is blocked from both exit paths. `MsgWithdrawFromTier` (the final withdrawal step) requires the position to be undelegated first — but `MsgTierUndelegate` is blocked. `MsgExitTierWithDelegation` is also blocked. There is no exit path that skips reward settlement.

---

### Impact Explanation

Users who have locked principal tokens in a tier position (via `MsgLockTier` or `MsgCommitDelegationToTier`) and have accrued non-zero bonus rewards cannot recover their principal tokens when the bonus pool is empty. Both exit paths fail atomically with `ErrInsufficientBonusPool`. The principal tokens remain locked in the module's per-position delegator accounts with no user-accessible exit. The only resolution is governance replenishing the rewards pool — a privileged action outside the user's control. This is structurally identical to the reported rate-limit pattern: a shared resource (pool balance vs. rate-limit quota) controlled by a privileged party (governance vs. security council) gates user fund access.

---

### Likelihood Explanation

The `BeginBlocker` drains the pool every block when there is a base-rewards shortfall. In a live network with many positions and a non-zero `TargetBaseRewardsRate`, the pool can be depleted without any malicious action. Additionally, a large position holder claiming a large accumulated bonus in a single `MsgClaimTierRewards` transaction can drain the pool in one block, immediately blocking all other users from exiting. The pool has no minimum reserve enforced for exit operations, so depletion is a realistic steady-state condition.

---

### Recommendation

Decouple reward settlement from the exit precondition. Specifically:

- In `MsgTierUndelegate` and `MsgExitTierWithDelegation`, attempt reward settlement but continue with the exit even if `ErrInsufficientBonusPool` is returned (forfeiting or deferring the bonus, similar to how `BeforeRedelegationSlashed` already handles this case silently to avoid chain halt).
- Alternatively, record the owed bonus as a claimable debt on the position and allow the exit to proceed; the debt is paid when the pool is replenished.
- At minimum, ensure that a user who has completed their exit commitment period can always undelegate and withdraw their principal regardless of pool state.

---

### Proof of Concept

1. User locks tokens via `MsgLockTier` with `trigger_exit_immediately = true`.
2. Time advances past `ExitUnlockAt` (exit commitment elapsed).
3. The bonus pool is drained to zero (by BeginBlocker top-ups or other users claiming).
4. User submits `MsgTierUndelegate`:
   - Calls `claimRewards` → `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` → returns `ErrInsufficientBonusPool`.
   - Transaction fails. Position remains delegated.
5. User submits `MsgExitTierWithDelegation`:
   - Same call chain → same failure.
6. User has no remaining exit path. Principal tokens are locked until governance replenishes the pool.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L152-170)
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
