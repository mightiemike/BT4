### Title
NFT Minted to Non-Creator Recipient Becomes Permanently Unburnable — (`x/nft/keeper/keeper.go`)

### Summary

`keeper.BurnNFT` requires the caller to simultaneously satisfy both `IsOwner` and `IsDenomCreator`. Because `MsgMintNFT` allows the denom creator (`Sender`) to mint to an arbitrary `Recipient`, any NFT minted to a non-creator address enters a state where no party can burn it: the recipient fails `IsDenomCreator`, and the creator fails `IsOwner`. The NFT persists in the store indefinitely and the supply counter cannot be decremented.

### Finding Description

`keeper.BurnNFT` enforces a conjunctive guard: [1](#0-0) 

Both checks are applied to the **same** `owner` address. There is no code path in `BurnNFT` that allows either check to be skipped.

`MsgMintNFT` in the msg server parses `Sender` and `Recipient` as independent addresses and passes them separately to `keeper.MintNFT`: [2](#0-1) 

`keeper.MintNFT` only checks that `sender` is the denom creator; the `owner` stored on the NFT is set to `recipient`, which can be any address: [3](#0-2) 

This creates the deadlock: after `MintNFT(sender=A, recipient=B)`, the NFT owner is B. Neither A (fails `IsOwner`) nor B (fails `IsDenomCreator`) can call `BurnNFT` successfully.

The existing keeper test explicitly demonstrates this deadlock and works around it only by transferring the NFT back to the creator before burning: [4](#0-3) 

A separate `BurnNFTUnverified` function (which skips `IsDenomCreator`) exists for IBC use, confirming the developers recognized the dual-check is problematic in cross-module contexts, but this function is not reachable via `MsgBurnNFT`: [5](#0-4) 

### Impact Explanation

Any NFT minted to a non-creator recipient is permanently unburnable via the standard `MsgBurnNFT` transaction path. The NFT remains in the KV store, the supply counter is never decremented, and neither the rightful owner nor the denom authority can remove it without a cooperative `MsgTransferNFT` back to the creator first. This breaks the invariant that a denom authority controls the lifecycle of NFTs in their collection.

### Likelihood Explanation

The `Recipient` field in `MsgMintNFT` is a documented, first-class feature (spec explicitly describes it). Any denom creator who mints to a recipient other than themselves — a normal and expected use case — will produce an unburnable NFT. No special attacker capability is required; the creator themselves triggers the condition through ordinary usage.

### Recommendation

Remove the `IsDenomCreator` check from `BurnNFT` and allow the NFT owner alone to burn their token. Alternatively, allow the denom creator to burn any NFT in their collection regardless of current ownership (matching the authority model). The `BurnNFTUnverified` function already implements the owner-only path and can serve as the basis for the fix.

### Proof of Concept

```go
// 1. accountA issues denom
suite.keeper.IssueDenom(ctx, "testdenom", "Test", "", "", accountA)

// 2. accountA mints tokenX to accountB (non-creator recipient)
err := suite.keeper.MintNFT(ctx, "testdenom", "token1", "nm", "uri", "data", accountA, accountB)
// err == nil, NFT exists, owner = accountB

// 3. accountB (owner) cannot burn — not the denom creator
err = suite.keeper.BurnNFT(ctx, "testdenom", "token1", accountB)
// err: "accountB is not the creator of testdenom: unauthorized"

// 4. accountA (creator) cannot burn — not the NFT owner
err = suite.keeper.BurnNFT(ctx, "testdenom", "token1", accountA)
// err: "accountA is not the owner of testdenom/token1: unauthorized"

// 5. NFT still exists, supply unchanged
suite.True(suite.keeper.HasNFT(ctx, "testdenom", "token1"))
```

### Citations

**File:** x/nft/keeper/keeper.go (L72-82)
```go
func (k Keeper) MintNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, sender, owner sdk.AccAddress,
) error {
	_, err := k.IsDenomCreator(ctx, denomID, sender)
	if err != nil {
		return err
	}

	return k.MintNFTUnverified(ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, owner)
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

**File:** x/nft/keeper/keeper.go (L163-179)
```go
// BurnNFTUnverified deletes a specified NFT without verifying if the owner is the creator of denom
// Needed for IBC transfer of NFT
func (k Keeper) BurnNFTUnverified(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
	if err != nil {
		return err
	}

	k.deleteNFT(ctx, denomID, nft)
	k.deleteOwner(ctx, denomID, tokenID, owner)
	k.decreaseSupply(ctx, denomID)

	return nil
```

**File:** x/nft/keeper/msg_server.go (L54-72)
```go
func (m msgServer) MintNFT(goCtx context.Context, msg *types.MsgMintNFT) (*types.MsgMintNFTResponse, error) {
	recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
	if err != nil {
		return nil, err
	}

	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.MintNFT(ctx, msg.DenomId, msg.Id,
		msg.Name,
		msg.URI,
		msg.Data,
		sender,
		recipient,
	); err != nil {
```

**File:** x/nft/keeper/keeper_test.go (L172-188)
```go
	// Transfer nft to an address which is not the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address, address2)
	suite.NoError(err)

	// BurnNFT should fail when address is not the creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the creator of %s: %s", address2, denomID, types.ErrUnauthorized))
	}

	// Transfer nft back to the creator of denom
	err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address2, address)
	suite.NoError(err)

	// BurnNFT shouldn't fail when NFT exists and address is owner of nft and creator of denom
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address)
	suite.NoError(err)
```
