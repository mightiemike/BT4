### Title
`LastBonusAccrual` Checkpoint Advances Unconditionally Even When Bonus Truncates to Zero — (`x/tieredrewards/keeper/bonus_rewards.go`, `claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` in `x/tieredrewards` calls `applyBonusAccrualCheckpoint` unconditionally — advancing `pos.LastBonusAccrual` to `blockTime` — before checking whether `totalBonus` is zero. When `computeSegmentBonus` truncates to zero due to integer division, the time window is permanently consumed: no bonus is paid, but the accrual clock advances. This is the direct Cosmos analog of H-04's `rewardsPerToken_.lastUpdated` advancing while `accumulated` stays constant.

---

### Finding Description

`computeSegmentBonus` computes:

```
tokens * bonusApy * durationSeconds / SecondsPerYear  →  TruncateInt()
``` [1](#0-0) 

where `SecondsPerYear = 31_557_600`. [2](#0-1) 

When `tokens * bonusApy * durationSeconds < SecondsPerYear`, the result truncates to zero. This is reachable for any position where `shares * tokensPerShare * bonusApy * durationSeconds < 31_557_600`. With a 4% APY tier and `tokensPerShare ≈ 1.0`, a position smaller than ~788,940,000 base units (~7.89 CRO) claiming every second yields zero bonus per call.

The critical flaw is in `processEventsAndClaimBonus`: `applyBonusAccrualCheckpoint` is called at line 215 **unconditionally**, before the `totalBonus.IsZero()` guard at line 219:

```go
applyBonusAccrualCheckpoint(&pos.Position, blockTime)   // line 215 — always runs
pos.UpdateLastKnownBonded(bonded)

if totalBonus.IsZero() {
    return sdk.NewCoins(), nil                           // returns, but checkpoint already advanced
}
``` [3](#0-2) 

`applyBonusAccrualCheckpoint` unconditionally writes `blockTime` into `pos.LastBonusAccrual`: [4](#0-3) 

The updated position is then persisted by every caller of `processEventsAndClaimBonus` regardless of whether bonus was paid: [5](#0-4) 

The next call to `processEventsAndClaimBonus` will use the advanced `LastBonusAccrual` as `segmentStart`: [6](#0-5) 

The time window `[old_LastBonusAccrual, blockTime]` is permanently lost — it will never be replayed.

---

### Impact Explanation

The corrupted value is `pos.LastBonusAccrual`. It advances past time intervals for which no bonus was computed or paid. Those intervals are irrecoverably discarded: the position owner receives fewer bonus rewards than they are entitled to under the tier's `BonusApy`. Over many short-interval claims, the cumulative loss can be significant relative to the expected payout.

The `SecondsPerYear` divisor is `31_557_600`. For a 4% APY tier with `tokensPerShare = 1.0`:
- A position of 1 CRO (10^8 base units) claiming every 1 second: `10^8 × 0.04 × 1 / 31_557_600 ≈ 0.127` → truncates to **0**, checkpoint advances.
- A position of 1 CRO claiming every 5 seconds: `10^8 × 0.04 × 5 / 31_557_600 ≈ 0.63` → still **0**, checkpoint advances.
- A position of 1 CRO claiming every 315 seconds (~5 min): `10^8 × 0.04 × 315 / 31_557_600 ≈ 0.399` → still **0**.

The threshold for non-zero bonus per second is `shares * tokensPerShare > SecondsPerYear / bonusApy`. For 4% APY this is ~788,940,000 base units (~7.89 CRO). Positions below this threshold lose all bonus if claimed at sub-threshold intervals.

---

### Likelihood Explanation

The entry path is any user-initiated transaction that triggers `claimRewards` or `processEventsAndClaimBonus`:
- `MsgClaimTierRewards`
- `MsgTierUndelegate`
- `MsgTierRedelegate`
- `MsgAddToTierPosition`
- `MsgClearPosition`
- `MsgExitTierWithDelegation` [7](#0-6) 

Any position owner with a small position who calls these messages at sub-threshold intervals will naturally lose rewards. No adversarial intent is required. The `MinLockAmount` is governance-controlled and may be set to values where this truncation is reachable. [8](#0-7) 

---

### Recommendation

Move `applyBonusAccrualCheckpoint` to execute **only when bonus is non-zero**, or alternatively accumulate sub-threshold fractional rewards in a persistent `LegacyDec` field on the position and only truncate at withdrawal time. The minimal fix mirrors the H-04 recommendation: do not advance `LastBonusAccrual` if no bonus was computed for the segment.

```go
// After computing totalBonus:
if totalBonus.IsZero() {
    return sdk.NewCoins(), nil  // do NOT advance LastBonusAccrual
}
applyBonusAccrualCheckpoint(&pos.Position, blockTime)
pos.UpdateLastKnownBonded(bonded)
```

This must be scrutinized against the `LastKnownBonded` and `LastEventSeq` update paths to ensure event reference counts are still decremented correctly even when bonus is zero.

---

### Proof of Concept

1. Governance creates a tier with `BonusApy = 0.04` (4%) and `MinLockAmount = 1_000_000` (0.01 CRO).
2. User locks 1 CRO (10^8 base units) via `MsgLockTier`.
3. User calls `MsgClaimTierRewards` every block (~5 seconds).
4. Each call: `computeSegmentBonus` = `10^8 × 1.0 × 0.04 × 5 / 31_557_600 ≈ 0.63` → `TruncateInt()` = **0**.
5. `applyBonusAccrualCheckpoint` advances `LastBonusAccrual` by 5 seconds.
6. After 1 year of 5-second claims: user receives **0 bonus** despite being entitled to `10^8 × 0.04 = 4,000,000` base units (~0.04 CRO). [9](#0-8) [3](#0-2)

### Citations

**File:** x/tieredrewards/keeper/bonus_rewards.go (L15-21)
```go
func applyBonusAccrualCheckpoint(pos *types.Position, blockTime time.Time) {
	accrualEnd := blockTime
	if pos.CompletedExitLockDuration(blockTime) {
		accrualEnd = pos.ExitUnlockAt
	}
	pos.UpdateLastBonusAccrual(accrualEnd)
}
```

**File:** x/tieredrewards/keeper/bonus_rewards.go (L23-46)
```go
// computeSegmentBonus computes bonus for a time segment using a snapshot rate.
// Formula: shares * tokensPerShare * tier.BonusApy * durationSeconds / SecondsPerYear
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier, segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
	if !pos.ExitUnlockAt.IsZero() && segmentEnd.After(pos.ExitUnlockAt) {
		segmentEnd = pos.ExitUnlockAt
	}

	if !segmentEnd.After(segmentStart) {
		return math.ZeroInt()
	}

	durationSeconds := int64(segmentEnd.Sub(segmentStart) / time.Second)
	if durationSeconds <= 0 {
		return math.ZeroInt()
	}

	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
}
```

**File:** x/tieredrewards/types/keys.go (L25-26)
```go
	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L123-131)
```go
		bonus, err := k.processEventsAndClaimBonus(ctx, pos)
		if err != nil {
			return nil, nil, err
		}
		totalBonus = totalBonus.Add(bonus...)

		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, nil, err
		}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L164-165)
```go
	bonded := pos.LastKnownBonded
	segmentStart := pos.LastBonusAccrual
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L215-221)
```go
	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}
```

**File:** doc/architecture/adr-006.md (L84-92)
```markdown
```go
type Tier struct {
    Id            uint32          // e.g. 1, 2, 3
    ExitDuration  time.Duration   // wait time after triggering exit before undelegate is allowed
    BonusApy      sdk.Dec         // fixed bonus APY (e.g. 0.04 = 4%/year); capped at 1.0
    MinLockAmount math.Int        // minimum lock amount for new positions
    CloseOnly     bool            // when true, no new positions allowed (see §2)
}
```
```

**File:** doc/architecture/adr-006.md (L196-225)
```markdown
### MsgClaimTierRewards Flow

```
-> Validate: owner match, position_ids non-empty, no duplicates
-> For each position: get position, validate ownership
-> Skip undelegated positions (return zero rewards for those)

-> For each delegated position:
     Phase 1 (Base — direct per-position withdraw):
     -> distribution.WithdrawDelegationRewards(delAddr, valAddr) -> rewards
     -> Rewards auto-route to the owner's bank balance via the WithdrawAddr
        configured at position creation. No module-side ratio math.
     -> Emit EventBaseRewardsClaimed; accumulate totalBase.

     Phase 2 (Bonus — lazy event replay):
     -> Walk pending validator events since pos.LastEventSeq
     -> For each bonded segment [segmentStart, eventTime):
        bonus += shares × event.TokensPerShare × apy × duration / SecondsPerYear
     -> Update bonded state based on event type (UNBOND→false, BOND→true, SLASH→unchanged)
     -> Decrement event reference count (garbage-collect if zero)
     -> Final segment: if bonded && val.IsBonded():
        bonus += shares × currentRate × apy × duration
     -> Advance LastBonusAccrual, LastEventSeq, LastKnownBonded
     -> If pool balance < bonus: fail atomically (user retries later)
     -> SendCoinsFromModule(rewards_pool, owner, bonus); emit EventBonusRewardsClaimed.

-> Persist each updated position
-> Emit aggregated EventTierRewardsClaimed with position_ids and totals
-> Return aggregated base_rewards, bonus_rewards, position_ids
```
```
