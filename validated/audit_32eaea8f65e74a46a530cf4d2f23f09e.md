### Title
Increased `MinLockAmount` via `UpdateTier` Blocks Partial Exits for Existing Positions - (File: x/tieredrewards/keeper/msg_server.go)

---

### Summary

`ExitTierWithDelegation` validates the remaining position amount against the **current** tier's `MinLockAmount` rather than the value that was in effect when the position was created. Because governance can raise `MinLockAmount` via `UpdateTier` at any time, users with existing positions may find that any partial exit is rejected even though their position was validly created under the old threshold.

---

### Finding Description

When a user calls `ExitTierWithDelegation` for a partial exit (i.e., `msg.Amount < positionAmount`), the keeper fetches the live tier and enforces the current `MinLockAmount` on the remaining balance: [1](#0-0) 

```go
tier, err := ms.getTier(ctx, pos.TierId)          // live tier, not creation-time tier
...
if !tier.MeetsMinLockRequirement(remainingPositionAmount) {
    return nil, errorsmod.Wrapf(types.ErrMinLockAmountNotMet,
        "remaining amount %s is below tier minimum %s", remainingPositionAmount, tier.MinLockAmount)
}
```

The tier is mutable: governance (the module authority) can call `UpdateTier` to raise `MinLockAmount` at any time while positions are open: [2](#0-1) 

No snapshot of `MinLockAmount` is stored in the `Position` struct at creation time, so there is no way to compare against the original threshold.

**Concrete scenario:**

1. `MinLockAmount = 100`. User calls `LockTier` / `CommitDelegationToTier` with `amount = 150`. Position is created validly.
2. Governance passes a proposal calling `UpdateTier` with `MinLockAmount = 200`.
3. User calls `ExitTierWithDelegation` with `amount = 50` (intending to keep 100 in the position).
4. `remainingPositionAmount ≈ 100 < 200` → `ErrMinLockAmountNotMet` is returned.
5. Any partial exit that leaves a remainder below 200 is now permanently rejected. The user is forced into a full exit even though their position was created and managed entirely within the rules that existed at creation time.

---

### Impact Explanation

Users with existing positions whose locked amount falls between the old and new `MinLockAmount` cannot perform any partial exit. They lose the ability to manage their position incrementally and are coerced into a full exit (undelegation + unbonding period) regardless of their intent. While funds are not permanently lost, the user's right to a partial exit — a right that existed when the position was opened — is silently revoked by a governance parameter change, with no recourse.

---

### Likelihood Explanation

`UpdateTier` is gated behind the module authority (governance), so the trigger requires a governance proposal to pass. Governance proposals are submitted by ordinary accounts and pass through normal on-chain voting. Any governance participant can propose a `MinLockAmount` increase for operational or economic reasons (e.g., raising the minimum to reduce dust positions). There is no protocol-level guard that prevents `UpdateTier` from being executed while positions are open, and no warning to existing position holders.

---

### Recommendation

Cache `MinLockAmount` (and any other exit-relevant tier parameters) inside the `Position` struct at creation time, analogous to how `ExitUnlockAt` is already stored at trigger time. During `ExitTierWithDelegation`, use the position's stored `MinLockAmount` for the remaining-balance check rather than the live tier value. Alternatively, enforce that `UpdateTier` cannot raise `MinLockAmount` while any positions for that tier are open (similar to how `deleteTier` already guards against active positions via `hasPositionsForTier`). [3](#0-2) 

---

### Proof of Concept

1. Deploy chain with `Tier{Id: 1, MinLockAmount: 100, ...}`.
2. Alice submits `MsgLockTier{Id: 1, Amount: 150, ...}` → position created, `pos.Id = 0`.
3. Governance submits and passes `MsgUpdateTier{Tier: {Id: 1, MinLockAmount: 200, ...}}`.
4. Alice submits `MsgExitTierWithDelegation{PositionId: 0, Amount: 50}`.
   - `remainingPositionAmount ≈ 100`.
   - `tier.MinLockAmount = 200` (live value).
   - `MeetsMinLockRequirement(100)` → `false`.
   - Transaction reverts with `ErrMinLockAmountNotMet`.
5. Alice submits `MsgExitTierWithDelegation{PositionId: 0, Amount: 100}` (leaving 50).
   - Same failure: `50 < 200`.
6. Alice is forced to call `MsgExitTierWithDelegation` with the full position amount or use the `TriggerExitFromTier` → `TierUndelegate` → `WithdrawFromTier` path, surrendering her partial-exit right entirely.

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L590-598)
```go
		tier, err := ms.getTier(ctx, pos.TierId)
		if err != nil {
			return nil, err
		}
		// actual remaining amount (post-transfer) must meet min lock.
		if !tier.MeetsMinLockRequirement(remainingPositionAmount) {
			return nil, errorsmod.Wrapf(types.ErrMinLockAmountNotMet,
				"remaining amount %s is below tier minimum %s", remainingPositionAmount, tier.MinLockAmount)
		}
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
