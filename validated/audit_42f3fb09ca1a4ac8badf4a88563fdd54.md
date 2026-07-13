### Title
`MsgUpdateTier` Changes `BonusApy` Without Settling Prior Accrual Period — (`x/tieredrewards/keeper/bonus_rewards.go`)

### Summary

The `x/tieredrewards` module computes bonus rewards lazily at claim time using the **current** `tier.BonusApy`. Governance can change `BonusApy` via `MsgUpdateTier` at any time without first settling accrued-but-unclaimed bonus for all open positions. When a user later claims, every historical segment since their last claim is recalculated at the new APY, not the rate in effect when those segments were earned.

### Finding Description

Bonus rewards are computed lazily. Each position stores `LastBonusAccrual` and `LastEventSeq`; at claim time the module replays validator events from that checkpoint forward and calls `computeSegmentBonus` for every bonded segment:

```go
// x/tieredrewards/keeper/bonus_rewards.go:25-46
func (k Keeper) computeSegmentBonus(pos types.PositionState, tier types.Tier,
    segmentStart, segmentEnd time.Time, tokensPerShare math.LegacyDec) math.Int {
    ...
    return tokens.
        Mul(tier.BonusApy).          // ← current tier APY, not historical
        MulInt64(durationSeconds).
        QuoInt64(types.SecondsPerYear).
        TruncateInt()
}
```

The `tier` argument is fetched from the live store at claim time. There is no snapshot of `BonusApy` stored per-segment, per-event, or per-position. Governance can execute `MsgUpdateTier` (confirmed in ADR-006 §3 and the `SetTier` path in `x/tieredrewards/keeper/tier.go:27-35`) to change `BonusApy` instantly with no forced settlement of open positions.

The ADR documents the formula as:

> `shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear`

where `bonusApy` is always the **current** tier value, not the value at the time the segment was earned.

### Impact Explanation

- **APY increase**: every position that has not claimed since before the governance change receives a retroactive windfall — bonus is overpaid from the rewards pool for the entire unclaimed period at the higher rate.
- **APY decrease**: every such position is underpaid — users lose bonus they legitimately earned under the old rate.

The corrupted value is the **bonus coin amount transferred from the `RewardsPoolName` module account to position owners**. Depending on direction, this either drains the pool faster than intended or silently confiscates earned rewards from users.

### Likelihood Explanation

- Governance changes to tier parameters are an explicitly supported, documented operation (`MsgUpdateTier`).
- Positions are long-lived (1-year or 5-year lock durations). Many positions will have large unclaimed accrual windows at any governance-change block.
- No on-chain mechanism forces settlement before the update; the ADR's security invariants section does not list this as a protected invariant.
- Any governance proposal that adjusts `BonusApy` — even a routine one — triggers the issue for all positions in that tier that have not claimed since the proposal passed.

### Recommendation

Before applying a `BonusApy` change in `MsgUpdateTier`, the module should record a **tier-level APY change event** (analogous to the existing `ValidatorEvent` log) so that `computeSegmentBonus` can use the APY that was in effect for each segment boundary. Alternatively, `MsgUpdateTier` should iterate all positions in the tier and force-settle bonus accrual up to the current block time before writing the new `BonusApy`. The latter is O(N) in position count but is the simpler correctness fix; the former is O(1) at update time and requires extending the segment-replay logic.

### Proof of Concept

1. Governance creates Tier 1 with `BonusApy = 0.04` (4 %).
2. Alice locks 1 000 000 tokens into Tier 1 at T=0. `LastBonusAccrual = T0`.
3. Six months pass (T=T0+6mo). Alice has earned ~20 000 tokens of bonus at 4 % APY. She has not claimed.
4. Governance passes `MsgUpdateTier` setting `BonusApy = 0.08` (8 %). `SetTier` writes the new value immediately with no settlement.
5. Alice calls `MsgClaimTierRewards` at T=T0+6mo+1block.
6. `computeSegmentBonus` replays the single segment `[T0, T0+6mo]` using `tier.BonusApy = 0.08`, paying ~40 000 tokens — double what was earned.
7. The rewards pool is drained by an extra ~20 000 tokens that were never budgeted for this period.

The reverse scenario (APY cut) silently confiscates ~20 000 tokens from Alice. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** x/tieredrewards/keeper/bonus_rewards.go (L25-46)
```go
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

**File:** x/tieredrewards/keeper/tier.go (L27-35)
```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
}
```

**File:** doc/architecture/adr-006.md (L85-94)
```markdown
type Tier struct {
    Id            uint32          // e.g. 1, 2, 3
    ExitDuration  time.Duration   // wait time after triggering exit before undelegate is allowed
    BonusApy      sdk.Dec         // fixed bonus APY (e.g. 0.04 = 4%/year); capped at 1.0
    MinLockAmount math.Int        // minimum lock amount for new positions
    CloseOnly     bool            // when true, no new positions allowed (see §2)
}
```

Tiers are managed by governance (`MsgAddTier`, `MsgUpdateTier`, `MsgDeleteTier`), stored as separate keyed entries.
```

**File:** doc/architecture/adr-006.md (L279-291)
```markdown
### Bonus Rewards (Lazy Validator Events)

Bonus rewards are computed lazily using a validator event log rather than eagerly settling all positions on every validator state change.

**Event recording:** Staking hooks record `ValidatorEvent` entries (types: `SLASH`, `UNBOND`, `BOND`) with `TokensPerShare` snapshots at O(1) cost. Each event stores a `ReferenceCount` equal to the number of delegated positions on that validator at event time (from `PositionCountByValidator`).

**Claim-time replay:** At claim time, `processEventsAndClaimBonus` walks pending events for each position (from `pos.LastEventSeq` forward). For each bonded segment between events, bonus is computed using the snapshot rate at the segment boundary. Events are reference-counted; the count is decremented when a position processes the event, and the event is garbage-collected when the count reaches zero.

**Bonded state tracking:** `LastKnownBonded` tracks whether the validator was bonded after the last event replay. Event types update this state: `UNBOND` sets it to false, `BOND` sets it to true, `SLASH` leaves it unchanged.

**Formula per segment:** `shares * tokensPerShare * bonusApy * durationSeconds / SecondsPerYear`

**Accrual cap:** Bonus accrual is capped at `ExitUnlockAt` when exiting. No bonus after exit commitment elapses.
```

**File:** doc/architecture/adr-006.md (L343-355)
```markdown
## 9. Security Invariants

- Only governance can manage tiers and params.
- Rewards accrue only when delegated to a bonded validator.
- No undelegation until exit commitment has elapsed.
- `MsgClearPosition` settles rewards before clearing exit to prevent bonus manipulation past `ExitUnlockAt`.
- Pool balance is never exceeded. User paths fail atomically; the `BeforeRedelegationSlashed` hook forfeits bonus silently if the pool is short, to avoid chain halt.
- Base-reward routing: `SetWithdrawAddr(posDelAddr, owner)` is installed at position creation and cleared on deletion, so every base-reward withdrawal lands on the owner.
- Auth-account materialisation: before the first delegate or undelegate on a position's delegator address, a `BaseAccount` must exist at that address. Both `MsgLockTier` and `MsgCommitDelegationToTier` reserve the account up front via `createPositionDelegatorAccount`.
- CloseOnly tiers block new positions while allowing exits.
- Bonus cannot be double-claimed: `LastEventSeq` prevents event replay, `LastBonusAccrual` prevents segment replay, `LastKnownBonded` prevents unbonded gap overpay.
- All delegation paths reject non-bonded validators (delegate, redelegate, transferDelegationToPosition, transferDelegationFromPosition).
- Vesting accounts are not allowed to create a new tier position.
```
