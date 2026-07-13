### Title
Insufficient Bonus Pool Balance Permanently Locks User Delegations in Positions — (File: `x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

In `x/tieredrewards`, the `processEventsAndClaimBonus` function performs a hard revert via `sufficientBonusPoolBalance` when the `rewards_pool` module account cannot cover accrued bonus rewards. Because every exit-path message handler calls `claimRewards` as a mandatory prerequisite, an empty or under-funded bonus pool permanently locks user delegations inside their positions with no recovery path available to the user.

---

### Finding Description

In `processEventsAndClaimBonus` (`claim_rewards.go` lines 230–232), after computing the total bonus owed, the function checks whether the `rewards_pool` module account holds sufficient balance:

```go
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err
}
``` [1](#0-0) 

`sufficientBonusPoolBalance` (`bonus_rewards.go` lines 48–61) returns `ErrInsufficientBonusPool` whenever the pool balance is below the computed bonus, with no partial-payment fallback: [2](#0-1) 

This error propagates through `claimRewards` (`claim_rewards.go` lines 87–103) and causes every message handler that calls it to revert:

| Handler | Call site |
|---|---|
| `TierUndelegate` | `msg_server.go` line 166 |
| `TierRedelegate` | `msg_server.go` line 229 |
| `AddToTierPosition` | `msg_server.go` line 314 |
| `ExitTierWithDelegation` | `msg_server.go` line 532 |
| `ClearPosition` | `msg_server.go` line 406 | [3](#0-2) [4](#0-3) 

The only message handler that does **not** call `claimRewards` is `WithdrawFromTier` (`msg_server.go` lines 470–516), but it only transfers the spendable coins already sitting in the position's delegator account — it cannot be used while the delegation is still active. To reach `WithdrawFromTier`, the user must first successfully call `TierUndelegate`, which itself requires `claimRewards` to succeed. [5](#0-4) 

The `ForceFullExitWithDelegation` migration helper (`force_exit.go` line 37) also calls `claimRewards` and is equally blocked: [6](#0-5) 

The `rewards_pool` module account is a finite resource. The `BeginBlocker` top-up mechanism (`abci.go`) replenishes only the `fee_collector` for base rewards; it does not replenish `rewards_pool`. No governance message in the module adds funds to `rewards_pool`. Once the pool is drained, there is no on-chain path to restore it, and the lock becomes permanent.

---

### Impact Explanation

Every user whose position has accrued non-zero bonus rewards at the moment the pool is exhausted loses the ability to:
- Undelegate their staking shares (`TierUndelegate`)
- Redelegate to another validator (`TierRedelegate`)
- Exit with their delegation returned (`ExitTierWithDelegation`)
- Clear a previously triggered exit (`ClearPosition`)

The staking delegation (the user's principal) remains locked inside the position's deterministically derived delegator account indefinitely. The exact corrupted value is the `stakingtypes.Delegation` held by the position's delegator address, which becomes permanently inaccessible to the position owner.

---

### Likelihood Explanation

The `rewards_pool` is a finite module account. As positions accumulate bonus rewards over time (proportional to `BonusApy × shares × duration`), the pool balance decreases monotonically. Any user can call `ClaimTierRewards` to drain the pool faster. Once the pool balance falls below the smallest pending bonus claim, all exit operations for all positions with non-zero accrued bonus revert. This is reachable by any unprivileged delegator through normal `MsgClaimTierRewards` transactions.

---

### Recommendation

Remove the hard revert from the exit path. When the bonus pool is insufficient, `processEventsAndClaimBonus` should either:
1. Pay out only what the pool can cover (partial payment) and advance the accrual checkpoint accordingly, or
2. Skip the bonus payment entirely for exit-triggering operations (`TierUndelegate`, `ExitTierWithDelegation`) while still advancing the checkpoint, so the user can recover their principal.

The `sufficientBonusPoolBalance` hard-revert guard is appropriate for a standalone `ClaimTierRewards` call but must not block the exit path.

---

### Proof of Concept

1. Governance creates a tier with `BonusApy > 0` and a finite `rewards_pool` balance.
2. Alice calls `MsgLockTier` → position created, delegation active, bonus accruing.
3. Over time (or via repeated `MsgClaimTierRewards` by other users), the `rewards_pool` balance drops below Alice's accrued bonus.
4. Alice calls `MsgTierUndelegate` → internally calls `claimRewards` → calls `processEventsAndClaimBonus` → `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool` → entire transaction reverts.
5. Alice calls `MsgExitTierWithDelegation` → same failure path.
6. Alice calls `MsgTriggerExitFromTier` (succeeds, does not call `claimRewards`), then calls `MsgClearPosition` → calls `claimRewards` → same failure.
7. Alice cannot reach `MsgWithdrawFromTier` because her delegation was never undelegated.
8. Alice's staking delegation is permanently locked in the position's delegator account with no on-chain recovery path.

### Citations

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

**File:** x/tieredrewards/keeper/msg_server.go (L166-166)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
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

**File:** x/tieredrewards/keeper/msg_server.go (L532-532)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
```

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```
