### Title
Bonus Reward Precision Loss in `computeSegmentBonus` Causes Systematic Underpayment — (File: `x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

The `computeSegmentBonus` function in `x/tieredrewards/keeper/bonus_rewards.go` computes tiered bonus rewards using a final `TruncateInt()` after dividing by `SecondsPerYear` (31,557,600). For small positions or short time segments — which arise naturally when validator state-change events (BOND/UNBOND/SLASH) are frequent — the numerator is smaller than the divisor and the result truncates to **zero**. This is the same class of precision loss as the external report: integer division truncation silently zeroes out a per-period fee/reward, causing systematic underpayment that accumulates over time.

---

### Finding Description

The formula in `computeSegmentBonus` is:

```
bonus = shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear
``` [1](#0-0) 

`SecondsPerYear` is the constant `31_557_600`. [2](#0-1) 

The intermediate multiplications are done in `math.LegacyDec` (18 decimal places), so no precision is lost there. The only truncation is the final `TruncateInt()`. Whenever:

```
shares × tokensPerShare × bonusApy × durationSeconds  <  31_557_600
```

the result is **zero** — the position holder receives no bonus for that segment.

**Concrete example — small position, short segment:**

| Parameter | Value |
|---|---|
| shares | 1,000,000 ubasecro (1 CRO) |
| tokensPerShare | 1.0 |
| bonusApy | 0.04 (4 %) |
| durationSeconds | 780 s (~13 min) |

```
1,000,000 × 1.0 × 0.04 × 780 / 31,557,600 = 0.988  →  TruncateInt() = 0
```

Every segment shorter than ~790 seconds yields **zero** bonus for a 1 CRO position. If validator events fire every ~13 minutes (plausible during network instability or frequent validator set rotations), the position holder accrues **zero** bonus for the entire period, against an expected annual bonus of 40,000 ubasecro.

**Larger position, same segment:**

| shares | Per-segment result |
|---|---|
| 100,000,000 ubasecro (100 CRO) | `100,000,000 × 0.04 × 780 / 31,557,600 ≈ 98.8` → 98 (1.2 % loss per segment) |
| 1,000,000,000 ubasecro (1000 CRO) | `1,000,000,000 × 0.04 × 780 / 31,557,600 ≈ 988` → 988 (0.12 % loss per segment) |

The loss is always in the same direction (always rounds down, never up), so it accumulates monotonically across every segment for every position.

**Where segments come from:** `processEventsAndClaimBonus` walks the validator event log and calls `computeSegmentBonus` once per bonded segment between consecutive `ValidatorEvent` entries. [3](#0-2) 

Each `SLASH`, `UNBOND`, or `BOND` event creates a new segment boundary. A validator that is frequently jailed/unjailed or that participates in many redelegations will produce many short segments, amplifying the precision loss for all positions delegated to it.

---

### Impact Explanation

- **Corrupted value:** The `bonus` field returned by `computeSegmentBonus` is systematically lower than the mathematically correct value. The difference is silently discarded; it is never credited to the position holder or returned to the pool.
- **Who is affected:** Every tiered-rewards position holder whose position is small or whose validator produces frequent events.
- **Magnitude:** For a 1 CRO position with 4 % APY and validator events every 13 minutes, the annual bonus is **zero** instead of 40,000 ubasecro. For a 100 CRO position under the same conditions, the annual loss is ~1–2 % of expected bonus. The loss scales inversely with position size and directly with event frequency.
- **Funds are not stolen** — they remain in the rewards pool — but position holders are permanently underpaid relative to the advertised APY.

---

### Likelihood Explanation

- Any unprivileged delegator can open a tiered position via `MsgLockTier` or `MsgCommitDelegationToTier` with a small amount.
- Validator events are recorded by staking hooks (`AfterValidatorBonded`, `AfterValidatorBeginUnbonding`, `BeforeValidatorSlashed`) and require no attacker action — they occur naturally during normal chain operation.
- The precision loss is therefore **always active** for small positions; it requires no special triggering. The severity scales with how busy the validator set is.

---

### Recommendation

Avoid dividing by `SecondsPerYear` as the last operation before truncation. Two equivalent mitigations (matching those suggested in the external report):

1. **Scale up before dividing:** Multiply by a precision factor (e.g., `1e6`) before the final `QuoInt64`, then divide out the factor after `TruncateInt`. This preserves sub-unit precision across segments.
2. **Accumulate in `math.LegacyDec` and truncate only at claim time:** Store accrued bonus as a `Dec` in the position state and only call `TruncateInt()` when coins are actually transferred, so fractional amounts carry forward rather than being discarded each segment.

---

### Proof of Concept

The following reproduces the zero-bonus scenario for a small position with a short segment:

```go
// In x/tieredrewards/keeper/bonus_rewards_test.go (or a new test file)
func (s *KeeperSuite) TestComputeSegmentBonus_PrecisionLossZero() {
    s.setupTier(1)
    tier, _ := s.keeper.Tiers.Get(s.ctx, 1)
    tier.BonusApy = sdkmath.LegacyNewDecWithPrec(4, 2) // 4%

    now := s.ctx.BlockTime()
    // 780-second segment (~13 minutes)
    segmentEnd := now.Add(780 * time.Second)

    // 1 CRO = 1_000_000 ubasecro
    shares := sdkmath.LegacyNewDec(1_000_000)
    tokensPerShare := sdkmath.LegacyOneDec()

    pos := types.PositionState{
        Delegation: &stakingtypes.Delegation{Shares: shares},
    }

    bonus := s.keeper.ComputeSegmentBonus(pos, tier, now, segmentEnd, tokensPerShare)

    // Expected (no truncation): 1_000_000 * 1.0 * 0.04 * 780 / 31_557_600 ≈ 0.988
    // Actual (TruncateInt):     0
    s.Require().True(bonus.IsZero(),
        "precision loss: 1 CRO position earns zero bonus for a 780-second segment, got %s", bonus)
}
```

The root cause is at: [4](#0-3)

### Citations

**File:** x/tieredrewards/keeper/bonus_rewards.go (L39-45)
```go
	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
```

**File:** x/tieredrewards/types/keys.go (L25-26)
```go
	// SecondsPerYear is 365.25 days, used to convert durations to years for bonus calculation.
	SecondsPerYear int64 = 31_557_600
```

**File:** doc/architecture/adr-006.md (L211-218)
```markdown
     -> Walk pending validator events since pos.LastEventSeq
     -> For each bonded segment [segmentStart, eventTime):
        bonus += shares × event.TokensPerShare × apy × duration / SecondsPerYear
     -> Update bonded state based on event type (UNBOND→false, BOND→true, SLASH→unchanged)
     -> Decrement event reference count (garbage-collect if zero)
     -> Final segment: if bonded && val.IsBonded():
        bonus += shares × currentRate × apy × duration
     -> Advance LastBonusAccrual, LastEventSeq, LastKnownBonded
```
