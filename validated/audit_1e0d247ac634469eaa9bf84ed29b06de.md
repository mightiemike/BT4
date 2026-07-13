### Title
Additive Per-Segment Truncation in Bonus Reward Accumulation Undercharges Position Owners — (File: x/tieredrewards/keeper/bonus_rewards.go)

---

### Summary

`computeSegmentBonus` applies `TruncateInt()` independently on every time-segment bonus value. `processEventsAndClaimBonus` sums these already-truncated integers. Each validator event (SLASH, UNBOND, BOND) creates a new segment boundary, and each boundary introduces a truncation loss of up to 1 token unit. With N pending validator events, up to N token units are silently lost from the position owner's bonus payout, accumulating in the `RewardsPool` module account and never disbursed.

---

### Finding Description

`computeSegmentBonus` computes the bonus for a single time segment as:

```
shares × tokensPerShare × bonusApy × durationSeconds / SecondsPerYear
```

and immediately truncates the result to an integer: [1](#0-0) 

`processEventsAndClaimBonus` iterates over all pending validator events for the position and accumulates the per-segment results into `totalBonus`: [2](#0-1) 

After the event loop, a final segment (from the last event to `blockTime`) is also computed and added: [3](#0-2) 

Because each call to `computeSegmentBonus` truncates independently, the total bonus paid is:

```
Σ TruncateInt(segmentBonus_i)
```

instead of the correct:

```
TruncateInt(Σ segmentBonus_i)
```

The difference is at most 1 token unit per segment. With N validator events pending since the last claim, the position owner can be underpaid by up to N token units. The underpaid amount is never recovered — it remains locked in the `RewardsPool` module account.

Validator events are recorded by staking hooks: [4](#0-3) 

Each SLASH, UNBOND, and BOND event appended to a validator's event log creates an additional segment boundary and an additional truncation loss for every position on that validator that has not yet claimed.

---

### Impact Explanation

Position owners receive strictly less bonus than they are entitled to. The shortfall is bounded by the number of validator events since the position's last claim. For a validator that is slashed, unbonded, and rebonded repeatedly over a long period, or for a position that is left unclaimed for many blocks during which the validator undergoes many state transitions, the accumulated underpayment grows proportionally. The underpaid tokens remain in the `RewardsPool` and are effectively unrecoverable by the position owner. This is a direct, concrete underpayment of user funds.

---

### Likelihood Explanation

Validator state transitions (slashing, jailing/unjailing, unbonding, rebonding) are normal on-chain events that any validator can experience over its lifetime. A position owner who does not claim frequently, or whose validator undergoes many state changes, will silently accumulate truncation losses. No privileged access or attacker-controlled input is required — the loss occurs automatically through normal protocol operation. The more events that accumulate before a claim, the larger the total underpayment.

---

### Recommendation

Accumulate the per-segment bonus in `math.LegacyDec` throughout the event loop and apply a single `TruncateInt()` only once at the end, before constructing the `sdk.Coin`. Replace the `math.Int` accumulator `totalBonus` in `processEventsAndClaimBonus` with a `math.LegacyDec` accumulator, sum the raw (non-truncated) decimal segment values, and truncate only when converting to coins. This mirrors the fix suggested in the external report: use the absolute accumulated value rather than summing individually rounded deltas.

---

### Proof of Concept

Consider a position with:
- `shares = 1000`, `tokensPerShare = 1.0`, `bonusApy = 0.04` (4%)
- 10 validator events, each creating a segment of exactly `(SecondsPerYear / 10) - 1` seconds (just under one-tenth of a year)

Each segment bonus (pre-truncation):
```
1000 × 1.0 × 0.04 × (SecondsPerYear/10 - 1) / SecondsPerYear
= 4 × (1 - 10/SecondsPerYear)
≈ 3.999...
```

`TruncateInt()` → **3** per segment.

Summed over 10 segments: **30** tokens paid.

Correct calculation (truncate once):
```
TruncateInt(10 × 3.999...) = TruncateInt(39.99...) = 39
```

**Underpayment: 9 token units** from 10 events. The loss scales linearly with the number of validator events pending at claim time. [5](#0-4) [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/bonus_rewards.go (L39-46)
```go
	tokens := pos.Delegation.Shares.Mul(tokensPerShare)

	return tokens.
		Mul(tier.BonusApy).
		MulInt64(durationSeconds).
		QuoInt64(types.SecondsPerYear).
		TruncateInt()
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-179)
```go
	totalBonus := math.ZeroInt()
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L206-213)
```go
	if bonded && val.IsBonded() {
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** doc/architecture/adr-006.md (L302-312)
```markdown
## 6. Hooks & Slashing

| Hook | Behavior |
|------|----------|
| **AfterValidatorBeginUnbonding** | Records `UNBOND` event with `TokensPerShare` snapshot at O(1). No position iteration. |
| **AfterValidatorBonded** | Records `BOND` event with `TokensPerShare` snapshot at O(1). No position iteration. |
| **AfterValidatorRemoved** | If no leftover events (`hasValidatorEvents` returns false): clears `ValidatorEventSeq`. If leftover events exist: skips cleanup (seq is still needed for pending position claims). |
| **BeforeValidatorSlashed** | Records `SLASH` event at O(1) with pre-slash `TokensPerShare` snapshot. No position iteration. `x/distribution` handles per-delegator slash accounting natively (each position's delegator records a `ValidatorSlashEvent`); unbonding-delegation and standard delegation slashes require no tier-side hook because every position is its own staking delegator. |
| **BeforeRedelegationSlashed** | Fires before staking's `Unbond` in `SlashRedelegation`. Routes via `RedelegationMappings[unbondingId]` to the affected position and runs `processEventsAndClaimBonus` against **pre-slash** shares. Base rewards auto-withdraw inside distribution's `BeforeDelegationSharesModified` (still fired by the subsequent `Unbond`). On full slash, `pos.Delegation` is set to nil and checkpoints reset. Bonus forfeits silently if the pool is insufficient (chain-halt avoidance). |
| **AfterRedelegationCompleted** | Iterates the hook's `completedIds` and removes each matching row from `RedelegationMappings`. |

```
