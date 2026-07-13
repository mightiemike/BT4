### Title
Bonus rewards computed using current delegation shares (spot value) instead of historical shares, enabling retroactive inflation via `AddToPosition` before claim — (`File: x/tieredrewards/keeper/bonus_rewards.go`)

---

### Summary

In `processEventsAndClaimBonus`, the `computeSegmentBonus` function uses `pos.Delegation.Shares` — the **current** spot value at claim time — to compute bonus rewards for **all historical time segments**. Because the `AddToPosition` message can increase a position's delegation shares at any time, an attacker can add a large delegation to their position immediately before claiming, causing every historical segment to be retroactively computed with the inflated current shares. This drains more bonus tokens from the rewards pool than the position legitimately earned.

---

### Finding Description

**Root cause — `computeSegmentBonus` uses a spot value for shares:** [1](#0-0) 

```go
tokens := pos.Delegation.Shares.Mul(tokensPerShare)

return tokens.
    Mul(tier.BonusApy).
    MulInt64(durationSeconds).
    QuoInt64(types.SecondsPerYear).
    TruncateInt()
```

`pos.Delegation.Shares` is the **current** delegation shares loaded at claim time, not a historical snapshot. The formula is applied identically for every segment — both past and present — using this single current value.

**How historical segments are replayed in `processEventsAndClaimBonus`:** [2](#0-1) 

```go
for _, entry := range events {
    evt := entry.Event
    if bonded {
        bonus := k.computeSegmentBonus(*pos, tier, segmentStart, evt.Timestamp, evt.TokensPerShare)
        totalBonus = totalBonus.Add(bonus)
    }
    ...
}
```

The `tokensPerShare` field in each event **is** correctly snapshotted at the time the event was recorded (via `AfterValidatorBeginUnbonding`, `AfterValidatorBonded`, `BeforeValidatorSlashed`): [3](#0-2) 

However, `pos.Delegation.Shares` — the other multiplicand — is **never snapshotted**. It is the live value from `getDelegation` at claim time: [4](#0-3) 

The loop in `processEventsAndClaimBonus` never updates `pos.Delegation.Shares` between segments. Every historical segment is multiplied by the same current shares value.

**The `AddToPosition` entry path:**

The existence of `msg_server_add_to_position_test.go` confirms a supported `MsgAddToPosition` transaction that increases the delegation of an existing position's delegator address. After this call, `getDelegation` returns the larger share count, which is then used retroactively across all historical segments.



**Analogy to the reference report:**

| Reference report | This repository |
|---|---|
| `floatingDebt` (spot) used instead of average | `pos.Delegation.Shares` (spot) used instead of historical snapshot |
| Attacker repays debt → lowers utilization → borrows at lower rate | Attacker adds delegation → inflates shares → claims inflated historical bonus |
| Manipulation within one block | Manipulation within one transaction (add then claim) |
| Corrupted value: fixed interest rate | Corrupted value: bonus rewards paid from `RewardsPoolName` |

---

### Impact Explanation

An attacker with an existing tiered-rewards position can:
1. Let time accumulate (building up historical segments with small shares).
2. Call `MsgAddToPosition` with a large amount, increasing `pos.Delegation.Shares`.
3. Immediately call `MsgClaimRewards`.

Every historical segment is now computed with the inflated current shares, yielding a bonus far exceeding what was earned. The excess is paid directly from the `RewardsPoolName` module account: [5](#0-4) 

The corrupted value is the `RewardsPoolName` balance — real bonded tokens are drained beyond the legitimate entitlement.

---

### Likelihood Explanation

- The attack requires only a normal delegator with an existing tiered-rewards position and sufficient funds to add to it.
- No privileged role, governance action, or leaked key is needed.
- `MsgAddToPosition` and `MsgClaimRewards` are both standard, unprivileged production transactions.
- The longer a position has been open without claiming, the larger the retroactive inflation.
- Likelihood: **Medium** — requires capital and patience, but is fully permissionless.

---

### Recommendation

Snapshot `pos.Delegation.Shares` at each validator event, analogous to how `tokensPerShare` is already snapshotted. Store a `sharesAtEvent` field alongside `TokensPerShare` in `ValidatorEvent`, and pass the snapshotted shares to `computeSegmentBonus` for each historical segment. Only the final (current) segment should use the live `pos.Delegation.Shares`.

---

### Proof of Concept

1. Alice creates a tiered-rewards position by delegating `1 CRO` to a bonded validator. A `BOND` validator event is recorded with `tokensPerShare = 1.0` and `sharesSnapshot` (missing — not stored).
2. 30 days pass. No additional events occur. Alice's position has accrued 30 days of historical bonus at `1 CRO` effective stake.
3. Alice calls `MsgAddToPosition` with `999 CRO`, increasing her delegation shares ~1000×.
4. Alice calls `MsgClaimRewards`. `processEventsAndClaimBonus` replays the 30-day segment using `pos.Delegation.Shares = 1000 shares` (current) × `tokensPerShare = 1.0` (snapshotted) = `1000 CRO` effective stake.
5. Alice receives ~1000× the bonus she legitimately earned for those 30 days, draining the rewards pool. [6](#0-5) [7](#0-6)

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

**File:** x/tieredrewards/keeper/claim_rewards.go (L142-213)
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

**File:** x/tieredrewards/keeper/hooks.go (L36-48)
```go
	tokensPerShare, err := h.k.getTokensPerShare(ctx, valAddr)
	if err != nil {
		return err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	_, err = h.k.appendValidatorEvent(ctx, valAddr, types.ValidatorEvent{
		Height:         sdkCtx.BlockHeight(),
		Timestamp:      sdkCtx.BlockTime(),
		EventType:      types.ValidatorEventType_VALIDATOR_EVENT_TYPE_UNBOND,
		TokensPerShare: tokensPerShare,
		ReferenceCount: count,
	})
```

**File:** x/tieredrewards/keeper/delegation.go (L72-87)
```go
func (k Keeper) getDelegation(ctx context.Context, delegatorAddress string) (*stakingtypes.Delegation, error) {
	delAddr, err := sdk.AccAddressFromBech32(delegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}
	// A position has at most one delegation.
	dels, err := k.stakingKeeper.GetDelegatorDelegations(ctx, delAddr, 1)
	if err != nil {
		return nil, err
	}
	if len(dels) == 0 {
		return nil, nil
	}
	d := dels[0]
	return &d, nil
}
```
