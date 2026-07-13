Let me check the validator events and position state logic to understand if there's a deduplication guard.

### Title
Duplicate Position ID in `MsgClaimTierRewards` Enables N-fold Bonus Reward Drain — (`x/tieredrewards/keeper/msg_server.go`, `claim_rewards.go`)

### Summary

`ClaimTierRewards` pre-fetches all positions into a slice before processing, with no deduplication of `PositionIds`. Because `claimRewardsAndUpdatesPositions` iterates the pre-fetched in-memory slice and each duplicate entry carries the **original stale `LastEventSeq`**, `processEventsAndClaimBonus` pays bonus rewards more than once for the same position within a single transaction, draining `RewardsPoolName`.

---

### Finding Description

**Entrypoint — `ClaimTierRewards` (msg_server.go lines 429–468):**

The handler first validates the message, then loops over every `posId` in `msg.PositionIds`, fetching each from state and appending to a local slice: [1](#0-0) 

`validateClaimRewards` performs only an ownership check: [2](#0-1) 

There is **no deduplication** of `posId` values. Submitting `PositionIds = [1, 1, 1]` causes three identical `PositionState` copies (all with the same stale `LastEventSeq`) to be appended to `positions`.

**Processing — `claimRewardsAndUpdatesPositions` (claim_rewards.go lines 106–135):** [3](#0-2) 

For each element in the pre-fetched slice, it calls `processEventsAndClaimBonus` and then `setPosition`. The critical flaw: `positions[1]` and `positions[2]` are independent in-memory copies fetched **before** any writes occurred. Their `LastEventSeq` and `LastBonusAccrual` fields are the original stale values.

**Bonus computation — `processEventsAndClaimBonus` (claim_rewards.go lines 142–251):** [4](#0-3) 

It reads `pos.LastEventSeq` from the in-memory struct. For `positions[1]`, this is the **old** sequence number.

**Iteration 0 (positions[0]):**
- `getValidatorEventsSince(ctx, valAddr, oldSeq)` returns events `[e6, e7, e8]`
- Bonus is computed and sent from `RewardsPoolName` to owner
- `decrementEventRefCount` is called for each event (if `ReferenceCount == 1`, events are deleted)
- `setPosition` writes `LastEventSeq=8`, `LastBonusAccrual=blockTime` to state

**Iteration 1 (positions[1], stale copy, `LastEventSeq = oldSeq`):**

*Case A — single position on validator (ref count was 1, events deleted):*
- `getValidatorEventsSince` returns `[]`
- `segmentStart = pos.LastBonusAccrual` (old value, not `blockTime`)
- The "current segment" bonus is computed for `[old_LastBonusAccrual, blockTime]` at the current rate and paid again [5](#0-4) 

*Case B — multiple positions on validator (ref count > 1, events still present):*
- `getValidatorEventsSince` returns `[e6, e7, e8]` again
- Full bonus is paid again, identical to iteration 0

**Iteration 2 (positions[2]):** Same as iteration 1 — a third payment.

---

### Impact Explanation

Each duplicate entry causes an additional `bankKeeper.SendCoinsFromModuleToAccount` from `types.RewardsPoolName` to the attacker's address: [6](#0-5) 

With `N` copies of the same position ID, the attacker receives up to `N × bonus` from the rewards pool in a single transaction. This directly drains the `RewardsPoolName` module account, reducing the bonus rewards available to all other legitimate participants.

---

### Likelihood Explanation

Any position owner can craft this transaction. No special privileges, governance access, or operator compromise is required. The only precondition is owning at least one tiered rewards position with accrued bonus rewards. The exploit is executable in a single signed transaction.

---

### Recommendation

1. **Deduplicate `PositionIds` in `ClaimTierRewards`** before the fetch loop, or reject the message in `msg.Validate()` if any ID appears more than once.
2. **Re-read position state inside `claimRewardsAndUpdatesPositions`** rather than operating on a pre-fetched slice, so that the second processing of any position ID sees the already-advanced `LastEventSeq` written by the first iteration.
3. Alternatively, track a `seen` map in the fetch loop and skip or error on duplicates.

---

### Proof of Concept

```go
// Keeper test: submit ClaimTierRewards with PositionIds=[1,1,1]
// Setup: position 1 owned by attacker, validator bonded, events e6..e8 pending
msg := &types.MsgClaimTierRewards{
    Owner:       attacker.String(),
    PositionIds: []uint64{1, 1, 1},
}
resp, err := ms.ClaimTierRewards(ctx, msg)
require.NoError(t, err)

// Assert: BonusRewards == 3 * singleClaimBonus  (exploit succeeds)
// Assert: RewardsPoolName balance decreased by 3 * singleClaimBonus
// Correct behavior: BonusRewards == 1 * singleClaimBonus
```

The root cause is confirmed at:
- Pre-fetch without deduplication: [1](#0-0) 
- Stale in-memory copy processed repeatedly: [7](#0-6) 
- Bonus paid unconditionally per iteration: [6](#0-5)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L434-446)
```go
	positions := make([]types.PositionState, 0, len(msg.PositionIds))
	for _, posId := range msg.PositionIds {
		pos, err := ms.getPositionState(ctx, posId)
		if err != nil {
			return nil, err
		}

		if err := ms.validateClaimRewards(pos.Position, msg.Owner); err != nil {
			return nil, err
		}

		positions = append(positions, pos)
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L175-181)
```go
func (k Keeper) validateClaimRewards(pos types.Position, owner string) error {
	if !pos.IsOwner(owner) {
		return types.ErrNotPositionOwner
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L106-135)
```go
func (k Keeper) claimRewardsAndUpdatesPositions(ctx context.Context, positions []types.PositionState) (sdk.Coins, sdk.Coins, error) {
	totalBase := sdk.NewCoins()
	totalBonus := sdk.NewCoins()

	for i := range positions {
		pos := &positions[i]

		if !pos.IsDelegated() {
			continue
		}

		base, err := k.claimBaseRewards(ctx, *pos)
		if err != nil {
			return nil, nil, err
		}
		totalBase = totalBase.Add(base...)

		bonus, err := k.processEventsAndClaimBonus(ctx, pos)
		if err != nil {
			return nil, nil, err
		}
		totalBonus = totalBonus.Add(bonus...)

		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return nil, nil, err
		}
	}

	return totalBase, totalBonus, nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L153-156)
```go
	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L239-239)
```go
	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
```
