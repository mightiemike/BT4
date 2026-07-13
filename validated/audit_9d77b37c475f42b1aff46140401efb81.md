### Title
Bonus Pool Depletion Permanently Locks User Principal When Tier Is Marked CloseOnly - (File: `x/tieredrewards/keeper/msg_validate.go`)

### Summary
When governance marks a tier `CloseOnly` and the bonus rewards pool is empty (or insufficient), every exit path for a delegated position fails atomically. `MsgClearPosition` and `MsgTierRedelegate` are hard-blocked by the `CloseOnly` guard; `MsgTierUndelegate` and `MsgExitTierWithDelegation` both call `claimRewards` before any mutation and fail when the pool cannot cover pending bonus. The user's staked principal is locked in the module with no reachable exit.

### Finding Description

The `x/tieredrewards` module enforces that rewards are settled before any position mutation. The ADR documents this explicitly:

> Reward settlement before mutations: MsgTierUndelegate, MsgTierRedelegate, MsgAddToTierPosition, MsgClearPosition, and MsgExitTierWithDelegation all claim rewards before modifying the position.

`validateClearPosition` blocks `ClearPosition` for any `CloseOnly` tier unconditionally: [1](#0-0) 

`MsgTierRedelegate` is also blocked by `CloseOnly`: [2](#0-1) 

That leaves only two exit paths: `MsgTierUndelegate` and `MsgExitTierWithDelegation`. Both call `claimRewards` before any state change. Inside `claimRewards`, the bonus calculation path fails atomically when `pool balance < bonus`: [3](#0-2) 

`ExitTierWithDelegation` calls `claimRewards` at line 532 before calling `transferDelegationFromPosition`: [4](#0-3) 

`transferDelegationFromPosition` additionally requires the validator to be bonded: [5](#0-4) 

The `ErrInsufficientBonusPool` error is defined as a hard failure: [6](#0-5) 

The `ForceFullExitWithDelegation` migration helper also calls `claimRewards` and would fail identically; it is also marked for deletion after v8:

<cite repo="Thankgo

### Citations

**File:** x/tieredrewards/keeper/msg_validate.go (L85-92)
```go
	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}
```

**File:** x/tieredrewards/keeper/msg_validate.go (L163-170)
```go
	tier, err := k.getTier(ctx, pos.TierId)
	if err != nil {
		return err
	}

	if tier.IsCloseOnly() {
		return types.ErrTierIsCloseOnly
	}
```

**File:** doc/architecture/adr-006.md (L219-220)
```markdown
     -> If pool balance < bonus: fail atomically (user retries later)
     -> SendCoinsFromModule(rewards_pool, owner, bonus); emit EventBonusRewardsClaimed.
```

**File:** x/tieredrewards/keeper/msg_server.go (L532-535)
```go
	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}
```

**File:** x/tieredrewards/keeper/transfer_delegation.go (L132-134)
```go
	if !validator.IsBonded() {
		return math.LegacyDec{}, math.LegacyDec{}, math.Int{}, types.ErrValidatorNotBonded
	}
```

**File:** x/tieredrewards/types/errors.go (L17-17)
```go
	ErrInsufficientBonusPool            = errors.Register(ModuleName, 12, "insufficient bonus pool balance")
```
