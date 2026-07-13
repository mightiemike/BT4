### Title
Governance Can Update Tier `ExitDuration` While Active Positions Exist, Retroactively Changing User Lock-Up Terms — (File: `x/tieredrewards/keeper/tier.go`)

---

### Summary

The `SetTier` function in `x/tieredrewards/keeper/tier.go` allows governance to overwrite all tier parameters — including `ExitDuration` — without checking whether active positions exist in that tier. The sibling function `deleteTier` explicitly guards against active positions, but `SetTier` has no equivalent guard. Because `TriggerExitFromTier` reads `tier.ExitDuration` from live state at call time, a governance-approved tier update can silently change the exit lock-up period for every user who has not yet triggered their exit, retroactively altering the terms under which they locked their funds.

---

### Finding Description

`SetTier` is the single write path for tier configuration used by governance messages, genesis, and chain upgrades:

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
``` [1](#0-0) 

The `deleteTier` function, by contrast, explicitly blocks removal when active positions exist:

```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
    hasPositions, err := k.hasPositionsForTier(ctx, tierId)
    ...
    if hasPositions {
        return types.ErrTierHasActivePositions
    }
    ...
}
``` [2](#0-1) 

This asymmetry is the root cause: deletion is guarded, but in-place parameter updates are not.

The exploitable consequence is in `TriggerExitFromTier`, which reads `tier.ExitDuration` from live state at the moment the user calls the message:

```go
tier, err := ms.getTier(ctx, pos.TierId)
...
pos.TriggerExit(sdkCtx.BlockTime(), tier.ExitDuration)
``` [3](#0-2) 

`ExitDuration` is not snapshotted into the position at creation time; it is resolved dynamically. Any governance-approved change to a tier's `ExitDuration` between position creation and the user's `TriggerExitFromTier` call silently applies the new duration to that user's position.

---

### Impact Explanation

A user who locks funds in Tier N expecting a 30-day exit window can find, after a governance vote passes, that the exit window has been extended to 180 days (or shortened to 0). The `ExitUnlockAt` timestamp is computed only when `TriggerExitFromTier` is called, so all users with untriggered exits in the affected tier are exposed. The corrupted value is the `ExitUnlockAt` field stored in each affected `Position`, which directly controls when `WithdrawFromTier` or `ExitTierWithDelegation` becomes available. Funds remain locked for a duration the user never agreed to. [4](#0-3) 

---

### Likelihood Explanation

Governance proposals are a standard on-chain mechanism reachable by any token holder with sufficient stake. A malicious or negligent governance proposal to update a tier's `ExitDuration` while positions are active is a realistic scenario — especially since the code itself documents `SetTier` as the intended governance write path and provides no in-protocol warning or guard against mid-operation updates. The `deleteTier` guard demonstrates the developers were aware of the active-position concern for destructive operations, making the omission for updates a concrete gap rather than an intentional design choice. [1](#0-0) 

---

### Recommendation

Mirror the `deleteTier` guard in `SetTier`: before overwriting a tier's parameters, check `hasPositionsForTier`. If active positions exist, either reject the update or restrict which fields may be changed (e.g., allow only future-facing fields, not `ExitDuration`). Alternatively, snapshot `ExitDuration` into the `Position` struct at creation time so that subsequent tier updates cannot retroactively alter a user's agreed-upon lock-up terms. [5](#0-4) 

---

### Proof of Concept

1. Governance creates Tier 1 with `ExitDuration = 30 days`.
2. Alice calls `MsgLockTier` for Tier 1, locking 10,000 CRO. Her position is created; `ExitDuration` is **not** stored in the position.
3. Governance submits and passes a `SetTier` proposal updating Tier 1's `ExitDuration` to `365 days`. `SetTier` succeeds because it performs no active-position check.
4. Alice calls `MsgTriggerExitFromTier`. The handler fetches the current tier (`getTier`) and calls `pos.TriggerExit(blockTime, tier.ExitDuration)` — now `365 days`.
5. Alice's `ExitUnlockAt` is set to `now + 365 days` instead of the `now + 30 days` she agreed to. Her funds are locked for an additional 335 days beyond her expectation. [6](#0-5) [7](#0-6)

### Citations

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

**File:** x/tieredrewards/keeper/tier.go (L37-52)
```go
func (k Keeper) deleteTier(ctx context.Context, tierId uint32) error {
	hasPositions, err := k.hasPositionsForTier(ctx, tierId)
	if err != nil {
		return err
	}
	if hasPositions {
		return types.ErrTierHasActivePositions
	}
	if err := k.Tiers.Remove(ctx, tierId); err != nil {
		if stderrors.Is(err, collections.ErrNotFound) {
			return errorsmod.Wrapf(types.ErrTierNotFound, "tier id %d", tierId)
		}
		return errorsmod.Wrapf(err, "%s (tier id %d)", types.ErrTierStore.Error(), tierId)
	}
	return nil
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
