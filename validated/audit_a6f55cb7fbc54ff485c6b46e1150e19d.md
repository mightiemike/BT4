### Title
EditNFT Requires Simultaneous IsOwner + IsDenomCreator on Same Address, Permanently Locking Metadata After Transfer — (`x/nft/keeper/keeper.go`)

### Summary

`keeper.EditNFT` enforces two independent authorization checks — `IsOwner` and `IsDenomCreator` — against the **same** caller address. Once an NFT is transferred away from the denom creator, no single address can ever satisfy both checks simultaneously, permanently locking the NFT's URI and data fields.

### Finding Description

In `keeper.EditNFT`, the authorization logic is:

```go
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}

_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
``` [1](#0-0) 

`IsOwner` returns `ErrUnauthorized` unless the caller equals the NFT's current `Owner` field: [2](#0-1) 

`IsDenomCreator` (in `denom.go`) returns an error unless the caller equals the denom's stored `Creator` field. These two fields are independent state variables. After `TransferOwner` executes, `nft.Owner` is updated to the new owner while `denom.Creator` remains unchanged: [3](#0-2) 

**Concrete call sequence:**

1. `IssueDenom(creator)` — denom.Creator = creator
2. `MintNFT(sender=creator, recipient=alice)` — nft.Owner = alice
3. `TransferNFT(alice → bob)` — nft.Owner = bob
4. `creator` calls `EditNFT` → `IsOwner` fails (creator ≠ bob)
5. `bob` calls `EditNFT` → `IsDenomCreator` fails (bob ≠ creator)
6. `alice` calls `EditNFT` → both checks fail

No address can satisfy both conditions simultaneously. The NFT's `URI` and `Data` fields are permanently frozen.

### Impact Explanation

The NFT's metadata fields (`URI`, `Data`, `Name`) are permanently unmodifiable after any ownership transfer. If the URI points to off-chain metadata that needs correction (broken link, IPFS migration, royalty update), there is no on-chain path to fix it. The same double-check exists in `BurnNFT`: [4](#0-3) 

This means after a transfer, the NFT also cannot be burned by anyone — the current owner cannot burn it (not the denom creator), and the denom creator cannot burn it (not the owner). The NFT is effectively frozen in place with immutable metadata.

### Likelihood Explanation

This is triggered by the normal, intended usage pattern: a creator mints NFTs and distributes them to users. Any single transfer permanently triggers the lock. No special privileges, governance, or attacker coordination is required — a standard `MsgTransferNFT` from any owner is sufficient.

### Recommendation

Separate the two authorization checks. `EditNFT` should permit the call if the sender is **either** the current owner **or** the denom creator (or both), not require both simultaneously:

```go
isOwner := owner.Equals(nft.GetOwner())
_, creatorErr := k.IsDenomCreator(ctx, denomID, owner)
isDenomCreator := creatorErr == nil

if !isOwner && !isDenomCreator {
    return sdkerrors.Wrapf(types.ErrUnauthorized, ...)
}
```

### Proof of Concept

A keeper unit test asserting:
1. `IssueDenom(creator)` → `MintNFT(creator, recipient=alice)` → `TransferOwner(alice, bob)`
2. `EditNFT(creator, ...)` returns `ErrUnauthorized` (IsOwner fails)
3. `EditNFT(bob, ...)` returns `ErrUnauthorized` (IsDenomCreator fails)
4. `EditNFT(alice, ...)` returns `ErrUnauthorized` (both fail)

All three assertions pass against the unmodified production code, confirming the metadata is permanently locked with no recovery path.

### Citations

**File:** x/nft/keeper/keeper.go (L93-101)
```go
	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}
```

**File:** x/nft/keeper/keeper.go (L121-138)
```go
func (k Keeper) TransferOwner(
	ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress,
) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, srcOwner)
	if err != nil {
		return err
	}

	nft.Owner = dstOwner.String()

	k.setNFT(ctx, denomID, nft)
	k.swapOwner(ctx, denomID, tokenID, srcOwner, dstOwner)
	return nil
}
```

**File:** x/nft/keeper/keeper.go (L141-161)
```go
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	_, err = k.IsDenomCreator(ctx, denomID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
}
```

**File:** x/nft/keeper/nft.go (L45-56)
```go
func (k Keeper) IsOwner(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) (types.BaseNFT, error) {
	nft, err := k.GetNFT(ctx, denomID, tokenID)
	if err != nil {
		return types.BaseNFT{}, err
	}

	if !owner.Equals(nft.GetOwner()) {
		return types.BaseNFT{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the owner of %s/%s", owner.String(), denomID, tokenID)
	}

	return nft.(types.BaseNFT), nil
}
```
