### Title
Retroactive `BonusApy` Application on Governance Tier Update Corrupts All Pending Bonus Rewards — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

`processEventsAndClaimBonus` fetches the **current** tier at claim time and applies its `BonusApy` to every historical segment since `LastBonusAccrual`. When governance updates a tier's `BonusApy` via `SetTier`, all positions in that tier have their entire unclaimed bonus history retroactively recalculated at the new rate. Users who claim before the update receive the old rate; users who claim after receive the new rate applied to the full unclaimed window — including time that predates the change.

---

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` fetches the live tier at claim time:

```go
tier, err := k.getTier(ctx, pos.TierId)   // line 167 — current tier, not historical
```

It then passes this single `tier` object into every segment computation, both for historical validator-event segments and the live tail:

```go
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)  // line 178
// ...
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)             // line 211
``` [1](#0-0) [2](#0-1) 

The ADR documents the per-segment formula as:

> `shares * tokensPerShare * bonusApy * durationSeconds / SecondsPerYear` [3](#0-2) 

`BonusApy` is a mutable field on `Tier`:

```proto
string bonus_apy = 3 [
  (cosmos_proto.scalar)  = "cosmos.Dec",
  ...
];
``` [4](#0-3) 

`SetTier` — the function called by governance messages, genesis, and chain upgrades — performs no check for active positions when **updating** an existing tier. Only `deleteTier` guards against active positions:

```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
    if err := tier.Validate(); err != nil {
        return err
    }
    if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil { ... }
    return nil
}
``` [5](#0-4) 

```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
    hasPositions, err := k.hasPositionsForTier(ctx, tierId)
    ...
    if hasPositions {
        return types.ErrTierHasActivePositions
    }
``` [6](#0-5) 

The position stores only `TierId` (a reference), not a snapshot of `BonusApy` at creation or at each claim:

```proto
uint32 tier_id = 3;
``` [7](#0-6) 

---

### Impact Explanation

**Scenario A — `BonusApy` increased (e.g., 4% → 8%):**
Every position in the tier that has not claimed since before the governance change will have its entire unclaimed window recalculated at 8%. A position that earned 30 days of 4% bonus receives 30 days of 8% bonus instead — double the intended payout. The rewards pool is drained at twice the expected rate for all lazy claimers.

**Scenario B — `BonusApy` decreased (e.g., 8% → 4%):**
Every position that has not claimed since before the change loses the bonus it legitimately earned at 8% for the pre-change period. The user's accrued but unclaimed rewards are silently cut in half.

In both cases the corrupted value is the `totalBonus` integer accumulated in `processEventsAndClaimBonus` and subsequently transferred from the `RewardsPoolName` module account to the owner:

```go
bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))
...
k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins)
``` [8](#0-7) 

---

### Likelihood Explanation

Governance tier updates are an explicitly supported and expected operation — the comment on `SetTier` names governance messages as a primary caller. Any legitimate governance proposal to adjust `BonusApy` (e.g., rebalancing incentives, correcting a misconfigured tier) silently corrupts all pending bonus rewards for every active position in that tier. No adversarial intent is required; the breakage is automatic and affects all lazy claimers.

---

### Recommendation

Snapshot `BonusApy` into the `Position` record at creation time (and update it at each claim checkpoint), so that `computeSegmentBonus` always uses the rate that was in effect during the segment being computed. Alternatively, record a `TierBonusApyHistory` keyed by `(tierId, blockTime)` and look up the rate that was active at each segment boundary, analogous to how `TokensPerShare` snapshots are already used for slash accounting.

---

### Proof of Concept

1. Governance passes a proposal calling `SetTier` with `BonusApy` changed from `0.04` to `0.08` on Tier 1.
2. Alice has a position in Tier 1 that has been delegated for 30 days without claiming.
3. Alice calls `MsgClaimTierRewards`.
4. `processEventsAndClaimBonus` fetches the current tier (now `BonusApy = 0.08`) at line 167.
5. All 30 days of segments are computed with `bonusApy = 0.08`.
6. Alice receives `amount * 0.08 * (30 days / 365 days)` instead of the correct `amount * 0.04 * (30 days / 365 days)`.
7. The `RewardsPoolName` module account is debited twice the intended amount.

Bob, who claimed the day before the governance change, correctly received 4% for his period. Alice, who waited, receives 8% for the same historical period — a direct accounting inconsistency caused by the mutable tier parameter being applied retroactively.

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L167-178)
```go
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L207-213)
```go
		currentRate, err := k.getTokensPerShare(ctx, valAddr)
		if err != nil {
			return nil, err
		}
		bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)
		totalBonus = totalBonus.Add(bonus)
	}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-239)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```

**File:** doc/architecture/adr-006.md (L289-289)
```markdown
**Formula per segment:** `shares * tokensPerShare * bonusApy * durationSeconds / SecondsPerYear`
```

**File:** proto/chainmain/tieredrewards/v1/types.proto (L22-27)
```text
  string bonus_apy = 3 [
    (cosmos_proto.scalar)  = "cosmos.Dec",
    (gogoproto.customtype) = "cosmossdk.io/math.LegacyDec",
    (gogoproto.nullable)   = false,
    (amino.dont_omitempty) = true
  ];
```

**File:** proto/chainmain/tieredrewards/v1/types.proto (L50-52)
```text
  // tier_id references the Tier this position belongs to.
  uint32 tier_id = 3;

```

**File:** x/tieredrewards/keeper/tier.go (L26-35)
```go
// SetTier writes a tier after validation. Used by governance messages, genesis, and chain upgrades.
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

**File:** x/tieredrewards/keeper/tier.go (L37-44)
```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
	hasPositions, err := k.hasPositionsForTier(ctx, tierId)
	if err != nil {
		return err
	}
	if hasPositions {
		return types.ErrTierHasActivePositions
	}
```
