### Title
Bonus Rewards Permanently Stranded on Redelegation Slash When Pool Is Insufficient - (`x/tieredrewards/keeper/slash.go`, `x/tieredrewards/keeper/claim_rewards.go`)

### Summary

In `slashRedelegationPosition`, when `processEventsAndClaimBonus` fails with `ErrInsufficientBonusPool`, the error is silently swallowed. However, `processEventsAndClaimBonus` has already advanced the position's `LastBonusAccrual` checkpoint and `LastEventSeq` (and decremented validator event reference counts) **before** it checks pool sufficiency. The position is then persisted with these advanced checkpoints. The accrued bonus for the consumed period is permanently unclaimable by the position owner — an exact analog to the AeraVault pattern where earned rewards remain stranded in a contract with no path to recovery.

---

### Finding Description

`processEventsAndClaimBonus` in `claim_rewards.go` advances the position's accrual checkpoints unconditionally before it checks whether the pool has sufficient balance to pay:

```
applyBonusAccrualCheckpoint(&pos.Position, blockTime)   // line 215 — checkpoint advanced
pos.UpdateLastKnownBonded(bonded)                        // line 217
...
if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
    return nil, err                                      // line 230-232 — returns error AFTER checkpoint advance
}
``` [1](#0-0) 

Inside the event-processing loop, `decrementEventRefCount` is also called for every processed event before the pool check, potentially garbage-collecting those events: [2](#0-1) 

In `slashRedelegationPosition`, the `ErrInsufficientBonusPool` error is explicitly swallowed:

```go
if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
    if errors.Is(err, types.ErrInsufficientBonusPool) {
        k.logger(ctx).Error("insufficient bonus pool during redelegation slash", ...)
        // error swallowed — pos already has advanced checkpoints
    } else {
        return err
    }
}
// ...
return k.setPositionWithState(ctx, pos, ...)  // persists advanced checkpoints
``` [3](#0-2) 

After `setPositionWithState` persists the updated position, `pos.LastBonusAccrual` is set to `blockTime` and `pos.LastEventSeq` is advanced past all processed events. Any subsequent call to `ClaimTierRewards` or any reward-settling handler will compute bonus only from `blockTime` forward — the accrual window that was consumed during the failed slash settlement is permanently gone.

The contrast with the normal user-driven path is instructive: for `ClaimTierRewards`, `AddToPosition`, `Redelegate`, and `ClearPosition`, an `ErrInsufficientBonusPool` failure is propagated atomically so the user can retry after the pool is replenished. [4](#0-3) 

The slash path deliberately breaks this invariant to avoid a chain halt, but does so by consuming the accrual period without paying — permanently stranding the earned bonus in the `RewardsPoolName` module account with no mechanism for the position owner to recover it.

---

### Impact Explanation

The position owner permanently loses all bonus rewards accrued from `pos.LastBonusAccrual` up to `blockTime` at the moment of the redelegation slash. The coins remain in the `tieredrewards` rewards pool module account but are no longer attributable to the position (its checkpoints have moved past the earning window). No message exists to reclaim them for the affected owner. This is a direct, irreversible financial loss to the user — the exact analog of rewards remaining stranded in the MerkleOrchard forever.

---

### Likelihood Explanation

The trigger requires two concurrent conditions:

1. **A pending redelegation is slashed.** Validator slashing is a normal, governance-enforced protocol event. Any position that called `MsgTierRedelegate` while the source validator was bonded will have an active redelegation entry and a `RedelegationMappings` entry.
2. **The bonus rewards pool is depleted at slash time.** The pool is funded externally via bank sends to the module account. It can be empty or near-empty at any point, especially early in a pool cycle or after a large batch of claims.

Neither condition requires attacker control — both are reachable through normal protocol operation. The combination is realistic in production.

---

### Recommendation

Do not advance the position's accrual checkpoints (`applyBonusAccrualCheckpoint`, `UpdateLastKnownBonded`, `UpdateLastEventSeq`) and do not decrement event reference counts until after the pool sufficiency check passes. If the pool is insufficient during a redelegation slash, either:

- **Option A (preferred):** Leave the position's checkpoints unchanged so the user can claim the accrued bonus later once the pool is replenished. Accept the chain-halt risk mitigation by still swallowing the error, but without consuming the accrual window.
- **Option B:** Separate checkpoint advancement from payment — record the owed amount in a per-position debt store and pay it lazily on the next successful claim.

Additionally, the `decrementEventRefCount` calls inside the event loop should be deferred until after the payment succeeds, to prevent premature garbage-collection of events that were never fully settled.

---

### Proof of Concept

1. User A creates a tier position and calls `MsgTierRedelegate` from validator V1 → V2 while V1 is bonded. A `RedelegationMappings[unbondingID → positionID]` entry is created.
2. The `tieredrewards` rewards pool is depleted (e.g., all prior claimants have drained it).
3. V1 is slashed. The Cosmos SDK staking module fires `BeforeRedelegationSlashed(unbondingID, sharesToUnbond)`.
4. `slashRedelegationPosition` is called. It calls `processEventsAndClaimBonus(&pos)`.
5. Inside `processEventsAndClaimBonus`: the event loop runs, `decrementEventRefCount` is called for each event, `applyBonusAccrualCheckpoint` advances `pos.LastBonusAccrual` to `blockTime`, `UpdateLastKnownBonded` updates bonded state. Then `sufficientBonusPoolBalance` returns `ErrInsufficientBonusPool`.
6. Back in `slashRedelegationPosition`: the error is swallowed. `setPositionWithState(ctx, pos, ...)` persists the position with the advanced `LastBonusAccrual = blockTime` and advanced `LastEventSeq`.
7. User A later calls `MsgClaimTierRewards`. `processEventsAndClaimBonus` starts from the new `LastBonusAccrual = blockTime` — the entire prior accrual window is gone. The bonus earned before the slash is permanently unclaimable. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-232)
```go
func (k Keeper) processEventsAndClaimBonus(ctx context.Context, pos *types.PositionState) (sdk.Coins, error) {
	// Rewards should have been claimed before undelegation
	if !pos.IsDelegated() {
		return sdk.NewCoins(), nil
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	events, err := k.getValidatorEventsSince(ctx, valAddr, pos.LastEventSeq)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	blockTime := sdkCtx.BlockTime()

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
		}

		// Update bonded state based on event type.
		switch evt.EventType {
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND:
			bonded = false
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_BOND:
			bonded = true
		case types.ValidatorEventType_VALIDATOR_EVENT_TYPE_SLASH:
			// Slash doesn't change bonded state.
		}

		segmentStart = evt.Timestamp
		pos.UpdateLastEventSeq(entry.Seq)

		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
	}

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

	applyBonusAccrualCheckpoint(&pos.Position, blockTime)
	// Persist the bonded state so the next replay starts correctly.
	pos.UpdateLastKnownBonded(bonded)

	if totalBonus.IsZero() {
		return sdk.NewCoins(), nil
	}

	bondDenom, err := k.stakingKeeper.BondDenom(ctx)
	if err != nil {
		return nil, err
	}

	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/slash.go (L19-77)
```go
func (k Keeper) slashRedelegationPosition(ctx context.Context, unbondingId uint64, sharesToUnbond math.LegacyDec) error {
	positionId, err := k.getRedelegationMapping(ctx, unbondingId)
	if errors.Is(err, collections.ErrNotFound) {
		return nil
	}
	if err != nil {
		return err
	}

	pos, err := k.getPositionState(ctx, positionId)
	if errors.Is(err, types.ErrPositionNotFound) {
		k.logger(ctx).Error("position not found during redelegation slash",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"error", err.Error(),
		)
		return nil
	}
	if err != nil {
		return err
	}

	if !pos.IsDelegated() {
		// Defensive
		k.logger(ctx).Error("delegation missing during BeforeRedelegationSlashed",
			"position_id", positionId,
			"unbonding_id", unbondingId,
			"shares_to_unbond", sharesToUnbond.String(),
		)
		return nil
	}

	dstValStr := pos.Delegation.ValidatorAddress

	// Settle bonus against PRE-slash shares.
	if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
		// Deliberately forgo bonus rewards if pool is insufficient to prevent chain halt.
		if errors.Is(err, types.ErrInsufficientBonusPool) {
			k.logger(ctx).Error("insufficient bonus pool during redelegation slash",
				"position_id", pos.Id,
				"error", err.Error(),
			)
		} else {
			return err
		}
	}

	fullSlash := sharesToUnbond.GTE(pos.Delegation.Shares)

	if fullSlash {
		pos.Delegation = nil
		pos.ClearBonusCheckpoints()
		return k.setPositionWithState(ctx, pos, &ValidatorTransition{PreviousAddress: dstValStr})
	}
	// In-memory only: the persisted Position carries no share count, and the
	// live delegation will reflect the post-Unbond shares on the next read.
	// Update the local copy so any follow-up logic in this call sees consistent state.
	pos.Delegation.Shares = pos.Delegation.Shares.Sub(sharesToUnbond)
	return k.setPositionWithState(ctx, pos, nil)
```

**File:** doc/architecture/adr-006.md (L293-295)
```markdown
**Insufficient pool handling:**
- **User-driven paths** (ClaimTierRewards, AddToPosition, Undelegate, Redelegate, ClearPosition): fail atomically. User retries after pool is replenished.

```

**File:** x/tieredrewards/keeper/hooks.go (L128-130)
```go
func (h Hooks) BeforeRedelegationSlashed(ctx context.Context, unbondingID uint64, sharesToUnbond sdkmath.LegacyDec) error {
	return h.k.slashRedelegationPosition(ctx, unbondingID, sharesToUnbond)
}
```
