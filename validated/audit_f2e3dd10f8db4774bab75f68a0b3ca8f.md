### Title
Depleted Bonus Rewards Pool Permanently Blocks User Undelegation and Exit — (`x/tieredrewards/keeper/msg_server.go`, `x/tieredrewards/keeper/claim_rewards.go`, `x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

Every user-initiated exit path from a tiered-rewards position (`MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgTierRedelegate`, `MsgClearPosition`) mandatorily calls `claimRewards()` before executing the delegation transfer. `claimRewards()` calls `processEventsAndClaimBonus()`, which hard-fails with `ErrInsufficientBonusPool` if the `tieredrewards` rewards pool module account cannot cover the accrued bonus. When the pool is empty or underfunded, users who have completed their exit lock duration cannot undelegate, redelegate, or even cancel their exit — their staked tokens are frozen in the position's delegator address indefinitely.

---

### Finding Description

`MsgTierUndelegate` is the mandatory prerequisite for `MsgWithdrawFromTier` (the only way to recover locked tokens). Its handler calls `claimRewards` unconditionally before undelegating: [1](#0-0) 

`claimRewards` calls `processEventsAndClaimBonus`, which computes accrued bonus and then calls `sufficientBonusPoolBalance`: [2](#0-1) 

`sufficientBonusPoolBalance` returns a hard error if the pool balance is below the owed bonus: [3](#0-2) 

The same mandatory `claimRewards` call appears in every other exit path:

- `ExitTierWithDelegation` at line 532 [4](#0-3) 
- `TierRedelegate` at line 229 [5](#0-4) 
- `ClearPosition` at line 406 [6](#0-5) 

The architecture document explicitly acknowledges this behavior as a design choice: [7](#0-6) 

However, the consequence is that a user who has fully completed their exit lock duration has **no available transaction path** to recover their staked tokens when the pool is empty. `MsgWithdrawFromTier` requires the position to be undelegated first, and undelegation is blocked. The `ForceFullExitWithDelegation` migration helper also calls `claimRewards` and would fail identically: [8](#0-7) 

---

### Impact Explanation

**Corrupted invariant:** A user who has locked tokens via `MsgLockTier`, triggered exit via `MsgTriggerExitFromTier`, and waited the full `ExitDuration` holds a legitimate, unconditional right to recover their principal. That right is violated when the bonus pool is empty.

The staked tokens sit in the position's per-position delegator address (a deterministic module-derived account). The owner has no direct key to that address. The only protocol path to move those tokens back is through `MsgTierUndelegate` → `MsgWithdrawFromTier`, both of which are blocked. The user's principal is frozen for an unbounded period until governance replenishes the pool.

---

### Likelihood Explanation

The bonus pool is funded externally (governance proposals or admin transfers). The `BeginBlocker` also drains the same pool to top up base rewards to the distribution module when the fee collector is insufficient: [9](#0-8) 

As the chain matures and the pool depletes through normal reward claims and BeginBlocker top-ups, any position with non-zero accrued bonus will be unable to exit. A user with a long-running position in a high-APY tier will have a large accrued bonus, making the pool shortfall more likely to trigger. No attacker action is required — normal protocol operation causes the pool to drain.

---

### Recommendation

Decouple the bonus claim from the exit/undelegation path. When the pool is insufficient, the exit operation should proceed and the owed bonus should be recorded as a claimable debt (stored on-chain), payable when the pool is replenished. Alternatively, allow `MsgTierUndelegate` and `MsgExitTierWithDelegation` to skip bonus payout when the pool is empty, recording the unclaimed amount for later settlement, so that principal recovery is never gated on pool solvency.

---

### Proof of Concept

1. User calls `MsgLockTier` with a high-APY tier, locking 10,000 CRO.
2. User calls `MsgTriggerExitFromTier` and waits for `ExitDuration` to elapse.
3. Over time, other users claim rewards and the BeginBlocker drains the `tieredrewards` rewards pool to zero.
4. User calls `MsgTierUndelegate`:
   - → `validateUndelegatePosition` passes (exit elapsed, delegated)
   - → `claimRewards` → `processEventsAndClaimBonus` computes, e.g., 500 CRO bonus
   - → `sufficientBonusPoolBalance(ctx, 500CRO)` checks pool balance = 0
   - → returns `ErrInsufficientBonusPool: bonus: 500cro, pool balance: 0cro`
   - → transaction reverts
5. User tries `MsgExitTierWithDelegation` — same `claimRewards` call at line 532, same failure.
6. User tries `MsgClearPosition` to cancel exit — same `claimRewards` call at line 406, same failure.
7. User's 10,000 CRO principal remains locked in the position delegator address with no available recovery path until governance replenishes the pool.

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

**File:** x/tieredrewards/keeper/msg_server.go (L406-409)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L97-100)
```go
	bonus, err := k.processEventsAndClaimBonus(ctx, &pos)
	if err != nil {
		return types.PositionState{}, nil, nil, err
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

**File:** doc/architecture/adr-006.md (L293-294)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.
```

**File:** x/tieredrewards/keeper/force_exit.go (L37-40)
```go
	posState, baseRewards, bonusRewards, err := k.claimRewards(ctx, posState)
	if err != nil {
		return fmt.Errorf("claim rewards for position %d: %w", posID, err)
	}
```

**File:** x/tieredrewards/keeper/abci.go (L96-111)
```go
	poolAddr := k.accountKeeper.GetModuleAddress(types.RewardsPoolName)
	poolBalance := k.bankKeeper.GetBalance(ctx, poolAddr, bondDenom)
	topUpAmount := shortFallAmount
	if poolBalance.Amount.IsZero() {
		k.logger(ctx).Error("base rewards pool is empty, cannot top up validator rewards",
			"shortfall", shortFallAmount.String(),
		)
		return nil
	}
	if poolBalance.Amount.LT(shortFallAmount) {
		k.logger(ctx).Error("base rewards pool has insufficient funds, distributing remaining balance",
			"shortfall", shortFallAmount.String(),
			"pool_balance", poolBalance.Amount.String(),
		)
		topUpAmount = poolBalance.Amount
	}
```
