### Title
Unbounded `ExitDuration` in `Tier.Validate()` Allows Governance to Permanently Lock User Funds - (`x/tieredrewards/types/tier.go`)

### Summary
`Tier.Validate()` enforces only a lower bound (`ExitDuration > 0`) on the exit lock duration, with no upper bound. A governance proposal via `MsgUpdateTier` can set `ExitDuration` to an arbitrarily large value (up to `math.MaxInt64` nanoseconds ≈ 292 years). Any user who calls `MsgTriggerExitFromTier` after such a change will have their `ExitUnlockAt` set centuries into the future, permanently blocking all three exit paths (`MsgTierUndelegate`, `MsgExitTierWithDelegation`, `MsgWithdrawFromTier`) and locking their staked tokens inside the module indefinitely.

### Finding Description

`Tier.Validate()` checks only that `ExitDuration` is strictly positive:

```go
if t.ExitDuration <= 0 {
    return fmt.Errorf("exit duration must be positive")
}
```

No maximum is enforced. [1](#0-0) 

`SetTier()` — called by both `MsgAddTier` and `MsgUpdateTier` — delegates all validation to `tier.Validate()`, so a tier with `ExitDuration = math.MaxInt64` (≈292 years) passes and is persisted. [2](#0-1) 

The `UpdateTier` handler in `msg_server_auth.go` calls `SetTier` directly after the authority check, with no additional duration guard: [3](#0-2) 

When a user calls `MsgTriggerExitFromTier`, the handler reads the current tier's `ExitDuration` and passes it to `pos.TriggerExit`:

```go
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
``` [4](#0-3) 

`TriggerExit` stores `ExitUnlockAt = blockTime + duration` on the position:

```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
    p.ExitTriggeredAt = blockTime
    p.ExitUnlockAt = blockTime.Add(duration)
}
``` [5](#0-4) 

All three exit paths gate on `CompletedExitLockDuration(blockTime)`, which requires `blockTime >= ExitUnlockAt`:

- `validateUndelegatePosition` (used by `MsgTierUndelegate`): [6](#0-5) 
- `validateWithdrawFromTier` (used by `MsgWithdrawFromTier`): [7](#0-6) 
- `validateExitTierWithDelegation` (used by `MsgExitTierWithDelegation`): [8](#0-7) 

With `ExitUnlockAt` set to year ~2262 (or beyond), none of these checks can ever pass in practice.

The `Position.ExitUnlockAt` field is stored per-position at trigger time and is never retroactively updated by a subsequent tier change. [9](#0-8)  Positions that have already triggered exit before the governance change are unaffected; only positions that trigger exit after the change are locked.

### Impact Explanation

Users who call `MsgTriggerExitFromTier` after a governance proposal sets `ExitDuration` to an extreme value (e.g., `math.MaxInt64` ≈ 292 years) will have their staked tokens permanently locked inside the `x/tieredrewards` module. The three exit paths — `MsgTierUndelegate`, `MsgExitTierWithDelegation`, and `MsgWithdrawFromTier` — all require `CompletedExitLockDuration` to return `true`, which will never happen within any realistic timeframe. The corrupted value is `Position.ExitUnlockAt`, which is set to a timestamp centuries in the future, making the position's principal irrecoverable.

### Likelihood Explanation

The entry path is a standard governance proposal (`MsgUpdateTier`) that passes all on-chain validation because `Tier.Validate()` has no upper bound on `ExitDuration`. A malicious or negligent governance proposal could set this value to `math.MaxInt64` or any multi-century duration. Governance voters may not notice an extreme duration encoded as a raw nanosecond integer in a protobuf field. The protocol itself provides no safeguard. The M-04 class of vulnerability — unbounded delay/duration parameter — is well-known and this module replicates the exact pattern.

### Recommendation

Add a maximum `ExitDuration` constant in `x/tieredrewards/types/tier.go` and enforce it in `Tier.Validate()`:

```go
const MaxExitDuration = 10 * 365 * 24 * time.Hour // e.g. 10 years

if t.ExitDuration > MaxExitDuration {
    return fmt.Errorf("exit duration must not exceed %s: got %s", MaxExitDuration, t.ExitDuration)
}
```

This mirrors the existing upper-bound pattern already applied to `BonusApy` (capped at 1.0) and `TargetBaseRewardsRate` (capped at 1.0). [10](#0-9) 

### Proof of Concept

1. Governance submits `MsgUpdateTier` with `ExitDuration = math.MaxInt64` (9223372036854775807 nanoseconds ≈ 292 years). This passes `Tier.Validate()` because `math.MaxInt64 > 0`. [1](#0-0) 

2. `SetTier` persists the tier with the extreme duration. [2](#0-1) 

3. A user with an active position calls `MsgTriggerExitFromTier`. The handler reads `tier.ExitDuration = math.MaxInt64` and calls `pos.TriggerExit(blockTime, math.MaxInt64)`, setting `ExitUnlockAt ≈ year 2262`. [11](#0-10) 

4. The user attempts `MsgTierUndelegate`. `validateUndelegatePosition` calls `pos.CompletedExitLockDuration(blockTime)` which returns `false` because `blockTime < ExitUnlockAt`. The transaction is rejected with `ErrExitLockDurationNotReached`. The same rejection applies to `MsgExitTierWithDelegation` and `MsgWithdrawFromTier`. [6](#0-5) 

5. The user's staked tokens remain locked in the `x/tieredrewards` module with no recoverable exit path.

### Citations

**File:** x/tieredrewards/types/tier.go (L15-17)
```go
	if t.ExitDuration <= 0 {
		return fmt.Errorf("exit duration must be positive")
	}
```

**File:** x/tieredrewards/types/tier.go (L27-30)
```go
	// Cap BonusApy at 100% to prevent governance from draining the rewards pool.
	if t.BonusApy.GT(math.LegacyOneDec()) {
		return fmt.Errorf("bonus apy must not exceed 1.0 (100%%): got %s", t.BonusApy)
	}
```

**File:** x/tieredrewards/keeper/tier.go (L27-34)
```go
func (k Keeper) SetTier(ctx context.Context, tier types.Tier) error {
	if err := tier.Validate(); err != nil {
		return err
	}
	if err := k.Tiers.Set(ctx, tier.Id, tier); err != nil {
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tier.Id)
	}
	return nil
```

**File:** x/tieredrewards/keeper/msg_server_auth.go (L57-82)
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
}
```

**File:** x/tieredrewards/keeper/msg_server.go (L361-368)
```go
	tier, err := ms.getTier(ctx, pos.TierId)
	if err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)

```

**File:** x/tieredrewards/types/position.go (L71-74)
```go
func (p *Position) TriggerExit(blockTime time.Time, duration time.Duration) {
	p.ExitTriggeredAt = blockTime
	p.ExitUnlockAt = blockTime.Add(duration)
}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L57-60)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L192-195)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L225-228)
```go
	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if !pos.CompletedExitLockDuration(sdkCtx.BlockTime()) {
		return types.ErrExitLockDurationNotReached
	}
```

**File:** x/tieredrewards/types/types.pb.go (L154-156)
```go
	ExitTriggeredAt time.Time `protobuf:"bytes,7,opt,name=exit_triggered_at,json=exitTriggeredAt,proto3,stdtime" json:"exit_triggered_at"`
	// exit_unlock_at is when the user can claim tokens (exit_triggered_at + tier.exit_duration).
	ExitUnlockAt time.Time `protobuf:"bytes,8,opt,name=exit_unlock_at,json=exitUnlockAt,proto3,stdtime" json:"exit_unlock_at"`
```
