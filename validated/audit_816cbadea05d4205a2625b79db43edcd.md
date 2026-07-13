Looking at the external report's vulnerability class — **a state variable updated unconditionally even when the underlying condition hasn't changed, preventing a beneficial state decay and enabling cheap exploitation** — I need to find an analog in chain-main where a checkpoint/flag is set unconditionally, causing incorrect time-based reward accounting.

After examining the tiered rewards module's bonus accrual logic, I found a concrete match.

---

### Title
`LastKnownBonded` Hardcoded to `true` on Redelegate Enables Incorrect Bonus Accrual for Unbonding Validator Period — (`x/tieredrewards/keeper/msg_server.go`)

### Summary

In `TierRedelegate`, after settling rewards for the source validator, the position's `LastKnownBonded` checkpoint is unconditionally set to `true` regardless of the destination validator's actual bonded state. If the destination validator is currently in `UNBONDING` status, this incorrect flag causes `processEventsAndClaimBonus` to compute and pay bonus rewards for the entire unbonding gap — a period during which no bonus should accrue.

### Finding Description

In `MsgTierRedelegate`, after `claimRewards` settles the source-validator bonus, the code resets the position's bonus checkpoints for the destination validator: [1](#0-0) 

```go
latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
...
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)  // LastKnownBonded = true, unconditionally
```

`latestSeq` is set to the latest event sequence for the destination validator. If the destination validator is currently unbonding, its `UNBOND` event was recorded at some seq ≤ `latestSeq`. That event is therefore **excluded** from future replay (only events with seq > `latestSeq` are processed).

When the destination validator later re-bonds, `AfterValidatorBonded` records a `BOND` event at seq > `latestSeq`. The next call to `processEventsAndClaimBonus` then: [2](#0-1) 

1. Starts with `bonded = pos.LastKnownBonded = true` (the incorrectly set flag).
2. Encounters the `BOND` event at time `T_bond`.
3. Since `bonded == true`, computes bonus for `[redelegate_time, T_bond]` — the entire unbonding gap — using `computeSegmentBonus`.
4. Pays this bonus from the rewards pool.

The design intent of `LastKnownBonded` is explicitly documented in the code: [3](#0-2) 

> "Use the persisted bonded state from the last replay, not a hardcoded default. This prevents overpaying bonus for unbonded gaps between claims."

`TierRedelegate` violates this invariant by hardcoding `true`.

### Impact Explanation

The rewards pool (`tieredrewards` module account) pays out bonus tokens for a time window during which the destination validator was unbonding and no bonus should have accrued. The corrupted value is `pos.LastKnownBonded` in the `Position` store entry, which directly drives the bonus segment computation formula: [4](#0-3) 

```
bonus = shares × tokensPerShare × tier.BonusApy × durationSeconds / SecondsPerYear
```

For a position with 10,000 CRO, 4% bonus APY, and a 21-day unbonding gap, the incorrect payout is approximately **23 CRO per position**. Any number of positions can be exploited simultaneously by the same owner across multiple redelegations.

### Likelihood Explanation

The Cosmos SDK's `BeginRedelegate` does not require the destination validator to be bonded — only that it exists and is not jailed. Validators regularly enter and exit the active set. An attacker who monitors validator status can:

1. Observe a validator entering `UNBONDING` state.
2. Submit `MsgTierRedelegate` to that validator (cost: one transaction fee).
3. Wait for the validator to re-enter the active set (common in competitive validator sets).
4. Submit `MsgClaimTierRewards` (cost: one transaction fee).

The total cost is two transaction fees. The benefit scales with position size, bonus APY, and the length of the unbonding gap.

### Recommendation

Replace the hardcoded `true` with the actual bonded state of the destination validator at redelegate time:

```go
dstVal, err := ms.stakingKeeper.GetValidator(ctx, dstValAddr)
if err != nil {
    return nil, err
}
pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), dstVal.IsBonded())
```

This mirrors the correct pattern used in `processEventsAndClaimBonus` where `val.IsBonded()` is checked before computing the final segment. [5](#0-4) 

### Proof of Concept

1. Validator V is bonded; `AfterValidatorBeginUnbonding` fires, recording `UNBOND` event at seq `N` for V.
2. User calls `MsgTierRedelegate(positionId, dstValidator=V)`.
   - `claimRewards` settles source-validator bonus correctly.
   - `getValidatorEventLatestSeq(V)` returns `N`.
   - `UpdateBonusCheckpoints(N, blockTime_T0, true)` — `LastKnownBonded = true` despite V being unbonding.
3. V re-enters the active set; `AfterValidatorBonded` fires, recording `BOND` event at seq `N+1`, timestamp `T_bond`.
4. User calls `MsgClaimTierRewards(positionId)`.
   - `processEventsAndClaimBonus` starts with `bonded = true`, `segmentStart = T0`.
   - Processes `BOND` event at `T_bond`: computes bonus for `[T0, T_bond]` — the full unbonding gap.
   - Pays bonus from rewards pool.
5. User receives bonus for a period when V was unbonding and no bonus should have been earned.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L257-263)
```go
	latestSeq, err := ms.getValidatorEventLatestSeq(ctx, dstValAddr)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.UpdateBonusCheckpoints(latestSeq, sdkCtx.BlockTime(), true)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L162-179)
```go
	// Use the persisted bonded state from the last replay, not a hardcoded default.
	// This prevents overpaying bonus for unbonded gaps between claims.
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual

	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	for _, entry := range events {
		evt := entry.Event

		if bonded {
			// Compute bonus for the bonded segment [segmentStart, eventTime]
			// using the snapshot rate at the event.
			bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
			totalBonus = totalBonus.Add(bonus)
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L201-213)
```go
	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return nil, err
	}
	// Defensive: validator bond status check
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L39-45)
```go
	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
```
