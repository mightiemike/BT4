### Title
Retroactive `BonusApy` Application on Unclaimed Rewards Allows Sandwich Extraction from Rewards Pool — (`x/tieredrewards/keeper/claim_rewards.go`)

---

### Summary

When governance executes `MsgUpdateTier` to raise a tier's `BonusApy`, the updated rate is applied retroactively to every unclaimed historical segment for every existing position in that tier. An attacker who observes a passing governance proposal can create a large position before execution and claim immediately after, receiving the new higher APY for the entire pre-update period. Independently, all existing long-term position holders who have not recently claimed will also receive a windfall at the new rate for their entire unclaimed history, draining the `RewardsPoolName` module account beyond its intended obligations.

---

### Finding Description

`processEventsAndClaimBonus` in `x/tieredrewards/keeper/claim_rewards.go` fetches the tier from current state at the moment of the claim call:

```go
tier, err := k.getTier(ctx, pos.TierId)   // line 167 — current tier, current BonusApy
```

This single `tier` object is then passed unchanged into `computeSegmentBonus` for **every** historical segment, including segments that elapsed before any governance update:

```go
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)  // line 178
// ...
bonus := k.computeSegmentBonus(*pos, tier, segmentStart, blockTime, currentRate)             // line 211
```

The formula applied is:

```
Bonus = Shares × TokensPerShare × tier.BonusApy × (SegmentDuration / SecondsPerYear)
```

`tier.BonusApy` is the **current** value from state, not the value that was in effect when each segment was earned. No per-position or per-segment APY snapshot is stored anywhere in the `Position` struct. [1](#0-0) [2](#0-1) [3](#0-2) 

The `Position` struct stores `LastBonusAccrual` (the timestamp of the last claim) and `LastEventSeq`, but no record of the APY that was active during any prior segment. [4](#0-3) 

`SetTier` (the write path used by governance) overwrites the tier record in place with no forced settlement of existing positions:

```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
    if err := tier.Validate(); err != nil { return err }
    if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil { ... }
    return nil
}
``` [5](#0-4) 

There is no call to `claimRewardsAndUpdateTierPositions` before the tier record is mutated, so all pending unclaimed bonus for every position in the tier is left to be settled later at the new rate. [6](#0-5) 

---

### Impact Explanation

**Corrupted value:** the `RewardsPoolName` module account balance is over-disbursed.

Two concrete impact paths:

1. **Sandwich by a new position.** Attacker creates a position with amount `A` just before the governance proposal executes. After execution they call `MsgClaimTierRewards`. The entire period since position creation (even if only seconds) is billed at `newAPY`. The marginal gain per second is `A × (newAPY − oldAPY) / SecondsPerYear`. For a large `A` and a large APY jump this is non-trivial, and the attacker can repeat across multiple blocks.

2. **Windfall for all existing positions.** Every position that has not claimed since before the update will have its entire unclaimed history recalculated at `newAPY`. For a position with principal `P` locked for `T` years, the overpayment is `P × (newAPY − oldAPY) × T`. Across many long-running positions this can exhaust the rewards pool entirely, causing `sufficientBonusPoolBalance` to fail for legitimate future claimants. [7](#0-6) 

---

### Likelihood Explanation

Governance-controlled tier updates are a documented, expected operation (`MsgUpdateTier`). Cosmos SDK governance proposals are public and have a deterministic execution block, giving any observer a precise window to front-run. The `MsgLockTier` entry path is fully unprivileged. No special role, leaked key, or social engineering is required — only tokens to stake and the ability to read the mempool/governance state. [8](#0-7) 

---

### Recommendation

1. **Settle before mutating.** In `SetTier`, call `claimRewardsAndUpdateTierPositions` for the affected tier ID before writing the new tier record. This forces all pending bonus to be settled at the old APY before the new one takes effect.

2. **Snapshot APY per segment.** Record `BonusApy` changes as a `ValidatorEvent`-style log entry keyed by tier and sequence number. In `processEventsAndClaimBonus`, look up the APY that was active during each segment rather than using the current tier value.

3. **Minimum lock before claiming.** Enforce a minimum time between position creation and first bonus claim to eliminate the economic incentive of the sandwich path. [6](#0-5) [5](#0-4) 

---

### Proof of Concept

**Setup:** Tier 1 has `BonusApy = 0.04` (4%). A governance proposal to set `BonusApy = 0.20` (20%) is in the voting period and has enough votes to pass.

**Steps:**

1. Attacker observes the proposal will pass at block `N`.
2. At block `N-1`, attacker submits `MsgLockTier{Id: 1, Amount: 10_000_000, ...}`. Position is created; `LastBonusAccrual = T_create`.
3. Block `N` executes: governance calls `SetTier` with `BonusApy = 0.20`. No settlement of existing positions occurs.
4. Attacker submits `MsgClaimTierRewards` at block `N+1`.
5. Inside `processEventsAndClaimBonus`:
   - `tier, _ = k.getTier(ctx, 1)` → `tier.BonusApy = 0.20` ← **new rate**
   - `segmentStart = T_create`, `blockTime = T_claim` (a few seconds later)
   - `computeSegmentBonus` applies `0.20` to the entire `[T_create, T_claim]` window
6. Attacker receives bonus at 20% APY for the full period, even though 4% was the contracted rate.

**Existing-position amplification:** A user who locked 1,000,000 tokens 1 year ago and has not claimed will now receive `1_000_000 × 0.20 × 1 = 200_000` tokens instead of the correct `1_000_000 × 0.04 × 1 = 40_000` tokens — a 5× overpayment of 160,000 tokens per such position, drawn from the shared rewards pool. [9](#0-8) [5](#0-4)

### Citations

**File:** x/tieredrewards/keeper/claim_rewards.go (L50-80)
```go
func (k Keeper) claimRewardsAndUpdateTierPositions(ctx context.Context, tierId uint32) error {
	ids, err := k.getPositionsIdsByTier(ctx, tierId)
	if err != nil {
		return err
	}
	if len(ids) == 0 {
		return nil
	}

	for _, id := range ids {
		pos, err := k.getPositionState(ctx, id)
		if err != nil {
			return err
		}
		if !pos.IsDelegated() {
			continue
		}

		if _, err := k.claimBaseRewards(ctx, pos); err != nil {
			return err
		}
		if _, err := k.processEventsAndClaimBonus(ctx, &pos); err != nil {
			return err
		}
		if err := k.setPosition(ctx, pos.Position, nil); err != nil {
			return err
		}
	}

	return nil
}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L161-213)
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
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L230-240)
```go
	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
```

**File:** x/tieredrewards/types/types.pb.go (L136-166)
```go
// Position represents a single lock position in the tier.
type Position struct {
	// id is the unique identifier for this position.
	Id uint64 `protobuf:"varint,1,opt,name=id,proto3" json:"id,omitempty"`
	// owner is the address that owns this position.
	Owner string `protobuf:"bytes,2,opt,name=owner,proto3" json:"owner,omitempty"`
	// tier_id references the Tier this position belongs to.
	TierId uint32 `protobuf:"varint,3,opt,name=tier_id,json=tierId,proto3" json:"tier_id,omitempty"`
	// last_bonus_accrual is the last time bonus rewards was claimed.
	LastBonusAccrual time.Time `protobuf:"bytes,4,opt,name=last_bonus_accrual,json=lastBonusAccrual,proto3,stdtime" json:"last_bonus_accrual"`
	// last_event_seq is the sequence number of the last validator event this
	// position has processed. Events with seq > last_event_seq are pending.
	LastEventSeq uint64 `protobuf:"varint,5,opt,name=last_event_seq,json=lastEventSeq,proto3" json:"last_event_seq,omitempty"`
	// last_known_bonded tracks whether the validator was bonded after the last
	// event replay. Used as the starting state for the next processEventsAndClaimBonus
	// call. True when the position is first created (only bonded validators accepted).
	LastKnownBonded bool `protobuf:"varint,6,opt,name=last_known_bonded,json=lastKnownBonded,proto3" json:"last_known_bonded,omitempty"`
	// exit_triggered_at is the time when exit was triggered. Zero value means not exiting.
	ExitTriggeredAt time.Time `protobuf:"bytes,7,opt,name=exit_triggered_at,json=exitTriggeredAt,proto3,stdtime" json:"exit_triggered_at"`
	// exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
	ExitUnlockAt time.Time `protobuf:"bytes,8,opt,name=exit_unlock_at,json=exitUnlockAt,proto3,stdtime" json:"exit_unlock_at"`
	// created_at_height is the block height when this position was created.
	CreatedAtHeight uint64 `protobuf:"varint,9,opt,name=created_at_height,json=createdAtHeight,proto3" json:"created_at_height,omitempty"`
	// created_at_time is the block time when this position was created.
	CreatedAtTime time.Time `protobuf:"bytes,10,opt,name=created_at_time,json=createdAtTime,proto3,stdtime" json:"created_at_time"`
	// delegator_address is the per-position account address that acts as the
	// delegator in x/staking, holds the principal during the lock period, and
	// is the withdraw source registered with x/distribution. Persisted at
	// creation; consumers must read this rather than recompute it.
	DelegatorAddress string `protobuf:"bytes,11,opt,name=delegator_address,json=delegatorAddress,proto3" json:"delegator_address,omitempty"`
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

**File:** x/tieredrewards/keeper/msg_server.go (L24-87)
```go
func (ms msgServer) LockTier(ctx context.Context, msg *types.MsgLockTier) (*types.MsgLockTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, msg.Id)
	if err != nil {
		return nil, err
	}

	if err := ms.validateNewPosition(ctx, msg.Owner, msg.Amount, tier); err != nil {
		return nil, err
	}

	valAddr, err := sdk.ValAddressFromBech32(msg.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(msg.Owner)
	if err != nil {
		return nil, err
	}

	id, err := ms.NextPositionId.Peek(ctx)
	if err != nil {
		return nil, err
	}
	delAddr, err := ms.createPositionDelegatorAccount(ctx, ownerAddr, id)
	if err != nil {
		return nil, err
	}

	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	if _, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount); err != nil {
		return nil, err
	}

	pos, err := ms.createDelegatedPosition(ctx, msg.Owner, tier, valAddr, delAddr, msg.TriggerExitImmediately)
	if err != nil {
		return nil, err
	}

	// Defensive, but should not happen since transactions are sequential
	if pos.Id != id {
		return nil, errorsmod.Wrapf(types.ErrInvalidPositionID, "position id mismatch: peeked %d, created %d", id, pos.Id)
	}

	if err := ms.setPosition(ctx, pos, &ValidatorTransition{PreviousAddress: ""}); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionCreated{
		Position: pos,
	}); err != nil {
		return nil, err
	}

	return &types.MsgLockTierResponse{PositionId: pos.Id}, nil
}
```
