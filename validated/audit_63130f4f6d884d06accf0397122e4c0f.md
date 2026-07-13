### Title
Governance `MsgUpdateTier` Can Retroactively Extend Exit Lock Duration for Existing Positions - (`x/tieredrewards/keeper/msg_server.go`)

---

### Summary

When a user creates a tier position (`MsgLockTier` / `MsgCommitDelegationToTier`), the `ExitDuration` they agreed to is **not stored on the position**. When they later call `MsgTriggerExitFromTier`, the exit duration is read **live** from the current tier state. Governance can pass `MsgUpdateTier` to increase `ExitDuration` at any time, retroactively extending the lock period for all existing positions that have not yet triggered exit — without any settlement, compensation, or opt-out mechanism.

---

### Finding Description

The `Position` struct stores `ExitTriggeredAt` and `ExitUnlockAt` only after exit is triggered, but it does **not** record the `ExitDuration` that was in effect at position creation time. [1](#0-0) 

When `MsgTriggerExitFromTier` is executed, the keeper fetches the tier's current `ExitDuration` live from state and uses it to compute `ExitUnlockAt`: [2](#0-1) 

`MsgUpdateTier` in `msg_server_auth.go` does include a guard — but only for `BonusApy` changes (it settles pending bonus rewards at the old rate before updating). There is **no analogous protection** for `ExitDuration` changes: [3](#0-2) 

The `Tier` struct's `ExitDuration` field is freely updatable by governance with no restriction on increasing it while active positions exist: [4](#0-3) 

---

### Impact Explanation

A user who locked tokens into a tier with `ExitDuration = 1 year` (as advertised at lock time) can have their exit window silently extended to, say, 5 years by a governance proposal. When they call `TriggerExitFromTier`, `ExitUnlockAt` is set to `now + 5 years` instead of `now + 1 year`. Their principal is locked for 4 additional years beyond what they agreed to. This is a direct loss of liquidity and economic freedom — the user cannot respond to market conditions, cannot access funds for other uses, and cannot exit on the timeline they committed to.

The corrupted value is: `Position.ExitUnlockAt` — it is computed from a live parameter that was not the one in effect at position creation, breaking the user's reasonable expectation of the lock commitment they made.

---

### Likelihood Explanation

The trigger is a governance proposal (`MsgUpdateTier`), which any token holder can submit and which passes by majority vote. This is a normal, supported production entrypoint — not a privileged key or social engineering. The M-06 reference report also involved an admin parameter update. The scenario is realistic: governance may legitimately want to extend exit durations for economic reasons (e.g., to reduce sell pressure), and the protocol provides no mechanism to grandfather existing positions. [5](#0-4) 

---

### Recommendation

Store the `ExitDuration` on the `Position` struct at creation time (analogous to how the original M-06 fix stored `originationFeeRate` on the loan struct). When `TriggerExitFromTier` is called, use `pos.ExitDuration` instead of `tier.ExitDuration`. Alternatively, `MsgUpdateTier` should iterate all active positions for the tier and, if `ExitDuration` increases, either reject the update or emit a warning and only apply the new duration to future positions. [6](#0-5) 

---

### Proof of Concept

1. Governance creates Tier 1 with `ExitDuration = 1 year`, `BonusApy = 4%`.
2. Alice calls `MsgLockTier(tier_id=1, amount=1_000_000, validator=V)`. She locks tokens expecting to be able to exit after 1 year.
3. Governance passes `MsgUpdateTier` setting `ExitDuration = 5 years` (all other fields unchanged). Because `BonusApy` is unchanged, `claimRewardsAndUpdateTierPositions` is **not** called — the update goes through silently.
4. Alice calls `MsgTriggerExitFromTier(position_id=Alice's)`. The keeper executes:
   ```go
   tier, _ := ms.getTier(ctx, pos.TierId)   // ExitDuration = 5 years now
   pos.TriggerExit(blockTime, tier.ExitDuration)  // ExitUnlockAt = now + 5 years
   ```
5. Alice's `ExitUnlockAt` is set 5 years in the future. She cannot undelegate or withdraw for 5 years, despite having committed to a 1-year lock. [2](#0-1) [7](#0-6)

### Citations

**File:** proto/chainmain/tieredrewards/v1/types.proto (L42-84)
```text
// Position represents a single lock position in the tier.
message Position {
  // id is the unique identifier for this position.
  uint64 id = 1;

  // owner is the address that owns this position.
  string owner = 2 [(cosmos_proto.scalar) = "cosmos.AddressString"];

  // tier_id references the Tier this position belongs to.
  uint32 tier_id = 3;

  // last_bonus_accrual is the last time bonus rewards was claimed.
  google.protobuf.Timestamp last_bonus_accrual = 4
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];

  // last_event_seq is the sequence number of the last validator event this
  // position has processed. Events with seq > last_event_seq are pending.
  uint64 last_event_seq = 5;

  // last_known_bonded tracks whether the validator was bonded after the last
  // event replay. Used as the starting state for the next processEventsAndClaimBonus
  // call. True when the position is first created (only bonded validators accepted).
  bool last_known_bonded = 6;

  // exit_triggered_at is the time when exit was triggered. Zero value means not exiting.
  google.protobuf.Timestamp exit_triggered_at = 7
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];

  // exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
  google.protobuf.Timestamp exit_unlock_at = 8
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];

  // created_at_height is the block height when this position was created.
  uint64 created_at_height = 9;

  // created_at_time is the block time when this position was created.
  google.protobuf.Timestamp created_at_time = 10
      [(gogoproto.nullable) = false, (gogoproto.stdtime) = true, (amino.dont_omitempty) = true];

  // delegator_address is the per-position account address that acts as the
  // delegator in staking
  string delegator_address = 11 [(cosmos_proto.scalar) = "cosmos.AddressString"];
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L347-386)
```go
func (ms msgServer) TriggerExitFromTier(ctx context.Context, msg *types.MsgTriggerExitFromTier) (*types.MsgTriggerExitFromTierResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateTriggerExit(pos.Position, msg.Owner); err != nil {
		return nil, err
	}

	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

	if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventExitTriggered{
		PositionId:   pos.Id,
		TierId:       pos.TierId,
		Owner:        pos.Owner,
		ExitUnlockAt: pos.ExitUnlockAt,
	}); err != nil {
		return nil, err
	}

	return &types.MsgTriggerExitFromTierResponse{
		ExitUnlockAt: pos.ExitUnlockAt,
		PositionId:   pos.Id,
	}, nil
}
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L57-81)
```go
func (ms msgServer) UpdateTier(ctx context.Context, msg *types.MsgUpdateTier) (*types.MsgUpdateTierResponse, error) {
	if err := ms.requireAuthority(msg.Authority); err != nil {
		return nil, err
	}

	oldTier, err := ms.getTier(ctx, msg.Tier.Id)
	if err != nil {
		return nil, err
	}

	if !oldTier.BonusApy.Equal(msg.Tier.BonusApy) {
		if err := ms.claimRewardsAndUpdateTierPositions(ctx, msg.Tier.Id); err != nil {
			return nil, err
		}
	}

	if err := ms.SetTier(ctx, msg.Tier); err != nil {
		return nil, err
	}

	if err := ms.emitTierChangedEvent(ctx, types.TierChangeAction_TIER_CHANGE_ACTION_UPDATE, msg.Tier); err != nil {
		return nil, err
	}

	return &types.MsgUpdateTierResponse{}, nil
```

**File:** x/tieredrewards/types/tier.go (L10-41)
```go
func (t Tier) Validate() error {
	if t.Id == 0 {
		return ErrInvalidTierID
	}

	if t.ExitDuration <= 0 {
		return fmt.Errorf("exit duration must be positive")
	}

	if t.BonusApy.IsNil() {
		return fmt.Errorf("bonus apy cannot be nil")
	}

	if t.BonusApy.IsNegative() {
		return fmt.Errorf("bonus apy cannot be negative: %s", t.BonusApy)
	}

	// Cap BonusApy at 100% to prevent governance from draining the rewards pool.
	if t.BonusApy.GT(math.LegacyOneDec()) {
		return fmt.Errorf("bonus apy must not exceed 1.0 (100%%): got %s", t.BonusApy)
	}

	if t.MinLockAmount.IsNil() {
		return fmt.Errorf("min lock amount cannot be nil")
	}

	if t.MinLockAmount.IsNegative() {
		return fmt.Errorf("min lock amount cannot be negative: %s", t.MinLockAmount)
	}

	return nil
}
```

**File:** proto/chainmain/tieredrewards/v1/tx.proto (L95-105)
```text
// MsgUpdateTier updates an existing tier.
message MsgUpdateTier {
  option (cosmos.msg.v1.signer) = "authority";
  option (amino.name)           = "chainmain/MsgUpdateTier";

  string authority = 1 [(cosmos_proto.scalar) = "cosmos.AddressString"];
  Tier   tier      = 2 [(gogoproto.nullable) = false, (amino.dont_omitempty) = true];
}

// MsgUpdateTierResponse defines the response for MsgUpdateTier.
message MsgUpdateTierResponse {}
```
