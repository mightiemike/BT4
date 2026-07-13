### Title
Duplicate Position ID in `MsgClaimTierRewards` Enables Double-Claiming of Tier Rewards - (File: `x/tieredrewards/keeper/msg_server.go`)

### Summary

`ClaimTierRewards` in `x/tieredrewards/keeper/msg_server.go` fetches all position states into an in-memory slice before passing them to `claimRewardsAndUpdatesPositions`. Because no deduplication is performed on the input `PositionIds`, a caller who submits the same position ID twice in a single transaction causes the same position's reward checkpoint to be processed twice, resulting in double the intended reward payout.

### Finding Description

`ClaimTierRewards` iterates over `msg.PositionIds`, fetches each position from the store, and appends it to a local slice:

```go
positions := make([]types.PositionState, 0, len(msg.PositionIds))
for _, posId := range msg.PositionIds {
    pos, err := ms.getPositionState(ctx, posId)
    ...
    positions = append(positions, pos)
}
totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
``` [1](#0-0) 

All reads from the store happen before any writes. If the same `posId` appears twice in `msg.PositionIds`, the `positions` slice contains two copies of the identical `PositionState` — both carrying the same unclaimed bonus checkpoints. When `claimRewardsAndUpdatesPositions` processes the slice, it computes and pays rewards for each element independently. The second element is the same stale in-memory snapshot as the first, so the same reward delta is computed and paid a second time.

This is structurally identical to the LlamaLocker bug: a batch loop decrements/consumes a value for each element without clearing the per-item state first, so the same item can appear twice and be consumed twice.

### Impact Explanation

A position owner can double-claim both base and bonus tier rewards in a single transaction by submitting `PositionIds: [posId, posId, ...]`. The `totalBase` and `totalBonus` coins minted/sent to the caller are multiplied by the number of duplicate occurrences. This drains the tiered-rewards module account faster than the protocol intends, directly corrupting the reward balance. [2](#0-1) 

### Likelihood Explanation

The entry path is a standard signed `MsgClaimTierRewards` transaction, reachable by any unprivileged account that owns at least one tiered-rewards position. No special role, leaked key, or social engineering is required. The attacker only needs to construct a message with a repeated position ID, which is trivially possible from the CLI or any client library.

### Recommendation

Before fetching positions, deduplicate `msg.PositionIds`. The simplest fix is to add a uniqueness check in `MsgClaimTierRewards.Validate()` (in `x/tieredrewards/keeper/msg_validate.go`) that returns an error if any position ID appears more than once. Alternatively, use a `map[uint64]struct{}` seen-set inside the loop in `ClaimTierRewards` and skip or error on duplicates. [3](#0-2) 

### Proof of Concept

1. Alice owns position `posId = 42` with accrued bonus rewards of 100 tokens.
2. Alice submits `MsgClaimTierRewards{Owner: alice, PositionIds: [42, 42]}`.
3. The loop fetches `posId=42` twice; both fetches return the same `PositionState` with the same unclaimed checkpoint.
4. `claimRewardsAndUpdatesPositions` processes both entries, computing 100 tokens of rewards for each.
5. Alice receives 200 tokens instead of 100, and the module account is debited 200 tokens.
6. The position's checkpoint is updated once (or twice to the same value), so the over-payment is not recoverable. [1](#0-0)

### Citations

**File:** x/tieredrewards/keeper/msg_server.go (L429-451)
```go
func (ms msgServer) ClaimTierRewards(ctx context.Context, msg *types.MsgClaimTierRewards) (*types.MsgClaimTierRewardsResponse, error) {
	if err := msg.Validate(); err != nil {
		return nil, err
	}

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

	totalBase, totalBonus, err := ms.claimRewardsAndUpdatesPositions(ctx, positions)
	if err != nil {
		return nil, err
	}
```
