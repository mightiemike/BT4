Looking at the repository structure, I need to find an analog to the "missing state check allows repeated action" vulnerability class. The external report describes a missing `hasVoted` check that allows repeated vote-power manipulation. The analog class here is: **duplicate claim — missing deduplication allows the same position to be processed multiple times in a single transaction, paying out bonus rewards multiple times while the position state is only updated once.**

Let me examine the critical path in `ClaimTierRewards`.

---

### Title
Duplicate Position IDs in `ClaimTierRewards` Allow Double-Spending of Bonus Rewards from the Rewards Pool — (`File: x/tieredrewards/keeper/msg_server.go`)

### Summary
`ClaimTierRewards` in `msg_server.go` loads position states for every ID in `msg.PositionIds` without deduplication. If the same position ID appears twice, `processEventsAndClaimBonus` is called twice on the same initial position snapshot, paying out bonus rewards twice while the on-chain position state is only advanced once — leaving the position eligible for the same double-claim in the next epoch.

### Finding Description

`ClaimTierRewards` builds a `positions` slice by iterating over `msg.PositionIds` and loading each position from state: [1](#0-0) 

No deduplication is performed. If `msg.PositionIds = [X, X]`, two independent copies of the same `PositionState` (with identical `LastEventSeq`) are appended to `positions`.

These are then passed to `claimRewardsAndUpdatesPositions`: [2](#0-1) 

Inside that function, each element is processed independently: [3](#0-2) 

For the duplicate entry, `processEventsAndClaimBonus` is called a second time on the original snapshot (same `LastEventSeq`), re-walking the same validator events and re-executing: [4](#0-3) 

This second `SendCoinsFromModuleToAccount` call transfers bonus rewards a second time from `RewardsPoolName` to the owner. The subsequent `setPosition` call at the end of each iteration persists the same final `LastEventSeq` value twice, so the on-chain position state appears as if it was only claimed once: [5](#0-4) 

The `decrementEventRefCount` calls inside `processEventsAndClaimBonus` act as a natural gate: [6](#0-5) 

If the validator event's reference count is already 1 (only one position delegated to that validator), the second decrement would fail and revert the transaction. However, when multiple positions are delegated to the same validator — the common case on a live network — the reference count is > 1, both decrements succeed, and the bonus is paid twice.

### Impact Explanation

The `RewardsPoolName` module account is drained at twice the intended rate per claim. A position owner with a position delegated to a popular validator (where ref count ≥ 2) can repeatedly submit `MsgClaimTierRewards` with `PositionIds = [X, X, X, ...]` to extract multiples of their entitled bonus rewards per transaction. The corrupted value is the `RewardsPoolName` balance and the `totalBonus` accounting returned to the caller. [7](#0-6) 

### Likelihood Explanation

Any unprivileged position owner can trigger this via a standard `MsgClaimTierRewards` transaction. The only precondition is that the validator the position is delegated to has at least one other position also delegated to it — a near-universal condition on a live network with multiple tiered-rewards users. The entry path is a normal CLI-signed transaction with no special permissions required. [8](#0-7) 

### Recommendation

Add a deduplication check in `ClaimTierRewards` before building the `positions` slice. Reject the message if any position ID appears more than once:

```go
seen := make(map[uint64]struct{}, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    if _, dup := seen[posId]; dup {
        return nil, errorsmod.Wrapf(sdkerrors.ErrInvalidRequest,
            "duplicate position id %d in claim request", posId)
    }
    seen[posId] = struct{}{}
    // ... existing load and validate logic
}
```

Alternatively, enforce uniqueness in `MsgClaimTierRewards.Validate()` so the check is applied at the message-validation layer before the handler is reached. [1](#0-0) 

### Proof of Concept

1. Alice has position ID `42` delegated to validator `V`. At least one other position (e.g., Bob's position `7`) is also delegated to `V`, so validator event ref counts are ≥ 2.
2. Alice submits `MsgClaimTierRewards{Owner: alice, PositionIds: [42, 42]}`.
3. The handler loads two copies of position `42`, both with `LastEventSeq = N`.
4. `claimRewardsAndUpdatesPositions` processes copy 1: events `N+1..M` are walked, ref counts decremented from 2→1, bonus `B` is sent from `RewardsPoolName` to Alice, position `42` is persisted with `LastEventSeq = M`.
5. Copy 2 is processed: same events `N+1..M` are walked (ref counts now 1→0), bonus `B` is sent again from `RewardsPoolName` to Alice, position `42` is persisted again with `LastEventSeq = M` (same value — no observable difference).
6. Alice receives `2B` bonus rewards. The position state shows `LastEventSeq = M`, identical to a legitimate single claim. The `RewardsPoolName` account is short by `B`.
7. Alice repeats each epoch, draining the rewards pool at double the intended rate. [9](#0-8)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-432)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}
```

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

**File:** x/tieredrewards/keeper/msg_server.go (L448-451)
```go
	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
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

**File:** x/tieredrewards/keeper/claim_rewards.go (L195-198)
```go
		// Decrement reference count.
		if err := k.decrementEventRefCount(ctx, valAddr, entry.Seq); err != nil {
			return nil, err
		}
```

**File:** x/tieredrewards/keeper/claim_rewards.go (L228-241)
```go
	bonusCoins := sdk.NewCoins(sdk.NewCoin(bondDenom, totalBonus))

	if err := k.sufficientBonusPoolBalance(ctx, bonusCoins); err != nil {
		return nil, err
	}

	ownerAddr, err := sdk.AccAddressFromBech32(pos.Owner)
	if err != nil {
		return nil, errorsmod.Wrap(sdkerrors.ErrInvalidAddress, "invalid owner address")
	}

	if err := k.bankKeeper.SendCoinsFromModuleToAccount(ctx, types.RewardsPoolName, ownerAddr, bonusCoins); err != nil {
		return nil, err
	}
```
