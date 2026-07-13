### Title
Stale Delegation Shares After `AddToTierPosition` — Return Value from `delegate()` Never Persisted to Position State - (File: `x/tieredrewards/keeper/msg_server.go`)

---

### Summary

In `AddToTierPosition`, the tiered-rewards module delegates additional tokens to a validator and receives back the newly minted `newShares`. Those shares are **never added to `pos.Delegation.Shares`** before the position is saved. Every subsequent operation that reads `pos.Delegation.Shares` — undelegation, redelegation, exit, and reward calculation — operates on a stale, under-counted share value, leaving the added delegation permanently inaccessible through the module.

---

### Finding Description

`AddToTierPosition` in `msg_server.go` executes the following sequence:

1. Locks the owner's funds and transfers them to the position's delegator account.
2. Calls `ms.delegate(ctx, delAddr, valAddr, msg.Amount)`, which returns `newShares` — the actual shares minted by the staking module for the added amount.
3. Passes `newShares` **only** to the event emitter.
4. Calls `ms.setPosition(ctx, pos.Position, nil)` to persist the position — but `pos.Delegation.Shares` was never incremented by `newShares`. [1](#0-0) 

The position's `Delegation.Shares` field therefore reflects only the shares that existed before the top-up. The staking module's on-chain delegation record is correct (it holds the full amount), but the tiered-rewards module's own accounting is permanently out of sync.

Every downstream operation reads `pos.Delegation.Shares` as the authoritative share count:

- **`TierUndelegate`** calls `ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)` — undelegates only the pre-addition shares, leaving the added shares bonded but unreachable. [2](#0-1) 

- **`TierRedelegate`** calls `ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)` — redelegates only the stale share count. [3](#0-2) 

- **`ExitTierWithDelegation`** calls `ms.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)` to compute `positionAmount`, then computes `remainingShares := pos.Delegation.Shares.Sub(unbondedShares)` — both values are wrong. [4](#0-3) [5](#0-4) 

The `delegate()` wrapper itself simply calls `k.stakingKeeper.Delegate(...)` and returns the shares the staking module assigned: [6](#0-5) 

---

### Impact Explanation

Any user who calls `AddToTierPosition` to top up their locked position will have the added tokens delegated on-chain but invisible to the tiered-rewards module. When the user later undelegates, redelegates, or exits, only the original (pre-top-up) share count is acted upon. The added delegation remains bonded under the position's delegator account with no module-level path to recover it. The user loses access to the topped-up funds for as long as the position exists, and potentially permanently if the position is deleted without sweeping the residual delegation.

Additionally, `ExitTierWithDelegation`'s minimum-lock check uses the stale `positionAmount`, so the guard can be bypassed or incorrectly applied, corrupting the invariant that a partial exit must leave at least `tier.MinLockAmount` behind.

---

### Likelihood Explanation

`AddToTierPosition` is a standard, unprivileged user-facing message. Any delegator who holds an active tiered position and calls `MsgAddToTierPosition` triggers the bug. No special role, leaked key, or social engineering is required. The path is: sign a `MsgAddToTierPosition` transaction → broadcast → bug fires deterministically.

---

### Recommendation

After `delegate()` returns, add the new shares to the position's stored delegation shares before persisting:

```go
newShares, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount)
if err != nil {
    return nil, err
}

// Fix: persist the updated share count
pos.Delegation.Shares = pos.Delegation.Shares.Add(newShares)

if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
    return nil, err
}
```

Alternatively, after the delegation call, re-read the actual delegation record from the staking keeper and use that as the authoritative share count, mirroring the balance-of pattern recommended in the original report.

---

### Proof of Concept

1. Alice creates a position via `LockTier` with 1000 CRO → position stores `Delegation.Shares = S₀`.
2. Alice calls `AddToTierPosition` with 500 CRO.
   - `delegate()` returns `newShares = S₁` (shares for 500 CRO).
   - `pos.Delegation.Shares` is **not** updated; position is saved with `S₀`.
3. Alice calls `TierUndelegate`.
   - `ms.undelegate(ctx, delAddr, valAddr, S₀)` is called.
   - Only the original 1000 CRO worth of shares is unbonded.
   - The 500 CRO worth of shares (`S₁`) remains bonded under `delAddr` with no module path to undelegate it.
4. After the unbonding period, Alice calls `WithdrawFromTier`.
   - `ms.bankKeeper.SpendableCoins(ctx, delAddr)` returns only the 1000 CRO that completed unbonding.
   - The 500 CRO is still delegated and inaccessible. [7](#0-6)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L182-184)
```go
	completionTime, _, err := ms.undelegate(ctx, delAddr, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
```

**File:** x/tieredrewards/keeper/msg_server.go (L245-247)
```go
	completionTime, unbondingID, err := ms.redelegate(ctx, delAddr, srcValAddr, dstValAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
```

**File:** x/tieredrewards/keeper/msg_server.go (L286-344)
```go
func (ms msgServer) AddToTierPosition(ctx context.Context, msg *types.MsgAddToTierPosition) (*types.MsgAddToTierPositionResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

	pos, err := ms.getPositionState(ctx, msg.PositionId)
	if err != nil {
		return nil, err
	}

	if err := ms.validateAddToPosition(ctx, pos, msg.Owner); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(msg.Owner)
	if err != nil {
		return nil, err
	}

	delAddr, err := sdk.AccAddressFromBech32(pos.DelegatorAddress)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid delegator address")
	}

	if err := ms.lockFunds(ctx, ownerAddr, delAddr, msg.Amount); err != nil {
		return nil, err
	}

	pos, _, _, err = ms.claimRewards(ctx, pos)
	if err != nil {
		return nil, err
	}

	valAddr, err := sdk.ValAddressFromBech32(pos.Delegation.ValidatorAddress)
	if err != nil {
		return nil, err
	}

	newShares, err := ms.delegate(ctx, delAddr, valAddr, msg.Amount)
	if err != nil {
		return nil, err
	}

	if err := ms.setPosition(ctx, pos.Position, nil); err != nil {
		return nil, err
	}

	sdkCtx := sdk.UnwrapSDKContext(ctx)
	if err := sdkCtx.EventManager().EmitTypedEvent(&types.EventPositionAmountAdded{
		PositionId:  pos.Id,
		TierId:      pos.TierId,
		Owner:       pos.Owner,
		SharesAdded: newShares,
		AmountAdded: msg.Amount,
	}); err != nil {
		return nil, err
	}

	return &types.MsgAddToTierPositionResponse{PositionId: pos.Id}, nil
```

**File:** x/tieredrewards/keeper/msg_server.go (L543-545)
```go
	positionAmount, err := ms.reconcileAmountFromShares(ctx, valAddr, pos.Delegation.Shares)
	if err != nil {
		return nil, err
```

**File:** x/tieredrewards/keeper/msg_server.go (L583-583)
```go
		remainingShares := pos.Delegation.Shares.Sub(unbondedShares)
```

**File:** x/tieredrewards/keeper/delegation.go (L42-53)
```go
func (k Keeper) delegate(ctx context.Context, delAddr sdk.AccAddress, valAddr sdk.ValAddress, amount math.Int) (math.LegacyDec, error) {
	val, err := k.stakingKeeper.GetValidator(ctx, valAddr)
	if err != nil {
		return math.LegacyDec{}, err
	}

	if !val.IsBonded() {
		return math.LegacyDec{}, types.ErrValidatorNotBonded
	}

	return k.stakingKeeper.Delegate(ctx, delAddr, amount, stakingtypes.Unbonded, val, true)
}
```
