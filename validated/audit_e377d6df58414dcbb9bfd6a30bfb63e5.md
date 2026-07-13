### Title
Bonus Pool Depletion Permanently Locks User Principal in `x/tieredrewards` — (`x/tieredrewards/keeper/msg_server.go`)

### Summary

Every mutation handler that modifies a delegated tier position (`MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgClearPosition`) calls `claimRewards` before executing its state change. `claimRewards` calls `processEventsAndClaimBonus`, which fails atomically with `ErrInsufficientBonusPool` if the rewards pool cannot cover accrued bonus. Because `MsgTierRedelegate` is separately blocked after exit commitment elapses (`ErrExitLockDurationElapsed`), a user whose exit has elapsed and whose position has accrued non-zero bonus has **no available exit path** when the pool is empty. The user's principal — the locked tokens held at the position's delegator address — is inaccessible until governance replenishes the pool. There is no mechanism for a user to forfeit accrued bonus and exit with only their principal.

### Finding Description

**Root cause — mandatory reward settlement before every exit mutation:**

`TierUndelegate` at `x/tieredrewards/keeper/msg_server.go:166`:
```go
pos, _, _, err = ms.claimRewards(ctx, pos)
if err != nil {
    return nil, err
}
```
`ExitTierWithDelegation` at `msg_server.go:532` and `ClearPosition` at `msg_server.go:406` contain the identical pattern. All three return the error to the caller, rolling back the entire transaction.

**Pool check — fails on any non-zero accrued bonus:**

`x/tieredrewards/keeper/bonus_rewards.go:48-61`:
```go
func (k Keeper) sufficientBonusPoolBalance(ctx context.Context, bonus sdk.Coins) error {
    if bonus.IsZero() {
        return nil
    }
    poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
    poolBalance := k.bankKeeper.GetAllBalances(ctx, poolAddr)
    if !poolBalance.IsAllGTE(bonus) {
        return errorsmod.Wrapf(types.ErrInsufficientBonusPool, ...)
    }
    return nil
}
```

**Redelegate is separately blocked after exit elapses:**

`x/tieredrewards/keeper/msg_validate.go:81-83`:
```go
if pos.HasTriggeredExit() && pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
    return types.ErrExitLockDurationElapsed
}
```

**`MsgWithdrawFromTier` does not call `claimRewards`** (`msg_server.go:470-516`), but it requires the position to already be undelegated (`validateWithdrawFromTier` returns `ErrPositionDelegated` if `pos.IsDelegated()`). Reaching `WithdrawFromTier` requires a prior successful `TierUndelegate`, which is blocked.

**Complete exit-path closure when pool is empty and bonus > 0:**

| Message | Outcome |
|---|---|
| `MsgTierUndelegate` | Fails: `ErrInsufficientBonusPool` |
| `MsgExitTierWithDelegation` | Fails: `ErrInsufficientBonusPool` |
| `MsgClearPosition` | Fails: `ErrInsufficientBonusPool` |
| `MsgTierRedelegate` | Fails: `ErrExitLockDurationElapsed` (exit elapsed) |
| `MsgWithdrawFromTier` | Fails: `ErrPositionDelegated` (still delegated) |

The position's principal — tokens held at `pos.DelegatorAddress` and delegated via the staking module — is inaccessible.

### Impact Explanation

User principal (the full locked token amount, e.g. a multi-year 5-year tier position) is permanently frozen at the position's per-position delegator address. The user cannot undelegate, exit with delegation, cancel exit, or redelegate. The only resolution is governance replenishing the `RewardsPoolName` module account. If governance is slow, unwilling, or unable to act, the lock is indefinite. This is not a reward loss — it is a principal lock: the user's own deposited tokens are inaccessible.

### Likelihood Explanation

The bonus pool is drained by two normal protocol flows:
1. The `BeginBlocker` continuously transfers shortfall from the pool to `x/distribution` to top up base rewards (ADR-006 §5).
2. Users claiming bonus rewards via `MsgClaimTierRewards` drain the pool directly.

No attacker action is required. A pool that starts funded will naturally deplete over time. Any position that has been delegated to a bonded validator for a non-trivial duration will have accrued non-zero bonus. Long-duration tiers (e.g. 5-year) with large locked amounts are the highest-risk positions. The scenario is realistic in any production deployment where pool replenishment is not automated.

### Recommendation

Add a `skip_bonus` or `force_exit` flag to `MsgTierUndelegate`, `MsgExitTierWithDelegation`, and `MsgClearPosition`. When set, the handler skips `processEventsAndClaimBonus` (forfeiting accrued bonus) and proceeds with the state mutation. This gives users a guaranteed exit path regardless of pool state, analogous to allowing message cancellation when the coordinator is unavailable. Alternatively, separate the reward-settlement step from the exit-authorization step so that a pool shortfall blocks only reward disbursement, not the exit itself.

### Proof of Concept

1. User calls `MsgLockTier` with a 5-year tier, locking 1,000,000 basecro to a bonded validator. Position is created with `DelegatorAddress = posDelAddr`.
2. One year passes. The position accrues substantial non-zero bonus (formula: `shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear`).
3. User calls `MsgTriggerExitFromTier`. `ExitUnlockAt` is set to `now + 5 years`.
4. Five years pass. Exit commitment elapses. Pool has been drained to zero by BeginBlocker and other claimants.
5. User calls `MsgTierUndelegate` → transaction fails with `ErrInsufficientBonusPool` at `msg_server.go:166-168`.
6. User calls `MsgExitTierWithDelegation` → fails with `ErrInsufficientBonusPool` at `msg_server.go:532-534`.
7. User calls `MsgClearPosition` → fails with `ErrInsufficientBonusPool` at `msg_server.go:406-408`.
8. User calls `MsgTierRedelegate` → fails with `ErrExitLockDurationElapsed` at `msg_validate.go:81-83`.
9. User calls `MsgWithdrawFromTier` → fails with `ErrPositionDelegated` because `TierUndelegate` never succeeded.
10. User's 1,000,000 basecro principal remains locked at `posDelAddr` with no exit path until governance replenishes the pool. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L152-168)
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
```

**File:** x/tieredrewards/keeper/msg_server.go (L388-408)
```go
func (ms msgServer) ClearPosition(ctx context.Context, msg *types.MsgClearPosition) (*types.MsgClearPositionResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateClearPosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	if !pos.HasTriggeredExit() {
		return &types.MsgClearPositionResponse{PositionId: pos.Id}, nil
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
```

**File:** x/tieredrewards/keeper/msg_server.go (L470-516)
```go
func (ms msgServer) WithdrawFromTier(ctx context.Context, msg *types.MsgWithdrawFromTier) (*types.MsgWithdrawFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateWithdrawFromTier(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	balances := ms.bankKeeper.SpendableCoins(ctx, delAddr)
	if !balances.IsZero() {
		if err := ms.bankKeeper.SendCoins(ctx, delAddr, ownerAddr, balances); err != nil {
			return nil, err
		}
	}

	if err := ms.deletePosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionWithdrawn{
		Position: pos.Position,
		Amount:   balances,
	}); err != nil {
		return nil, err
	}

	return &types.MsgWithdrawFromTierResponse{
		Amount: balances,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L518-534)
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

**File:** x/tieredrewards/keeper/msg_validate.go (L67-103)
```go
func (k Keeper) validateRedelegatePosition(ctx context.Context, pos types.PositionState, owner, dstValidator string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	if !pos.IsDelegated() {
		return types.ErrPositionNotDelegated
	}

	if pos.Delegation.ValidatorAddress == dstValidator {
		return types.ErrRedelegationToSameValidator
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if pos.HasTriggeredExit() && pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationElapsed
	}

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}

	isRedelegating, err := k.isRedelegating(ctx, pos.DelegatorAddress)
	if err != nil {
		return err
	}
	if isRedelegating {
		return errorsmod.Wrapf(types.ErrActiveRedelegation, "position %d has an active redelegation", pos.Id)
	}

	return nil
}
```

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
