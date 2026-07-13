### Title
`BurnNFT` and `EditNFT` Permanently Block Operations When NFT Owner Differs from Denom Creator - (File: `x/nft/keeper/keeper.go`)

### Summary

`BurnNFT` and `EditNFT` in the NFT keeper require the caller to simultaneously be both the **NFT owner** and the **denom creator**. Because NFTs can be freely transferred to non-creator addresses via `MsgTransferNFT`, any NFT held by a non-creator address becomes permanently unburnable and uneditable — a permanent lock of NFT state triggered by normal user transactions.

### Finding Description

`BurnNFT` enforces two independent role checks in sequence:

```go
// x/nft/keeper/keeper.go lines 141-161
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
    ...
    nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // must be NFT owner
    if err != nil {
        return err
    }

    _, err = k.IsDenomCreator(ctx, denomID, owner)         // AND must be denom creator
    if err != nil {
        return err
    }
    ...
}
```

`EditNFT` has the identical dual-role requirement:

```go
// x/nft/keeper/keeper.go lines 85-118
func (k Keeper) EditNFT(...) error {
    nft, err := k.IsOwner(ctx, denomID, tokenID, owner)   // must be NFT owner
    ...
    _, err = k.IsDenomCreator(ctx, denomID, owner)         // AND must be denom creator
    ...
}
```

The normal NFT lifecycle separates these two roles:

1. Address A calls `MsgIssueDenom` → A becomes denom creator.
2. Address A calls `MsgMintNFT` with `Recipient = B` → B becomes NFT owner, A remains denom creator.
3. Address B calls `MsgBurnNFT` → **fails**: B is owner but not creator.
4. Address A calls `MsgBurnNFT` → **fails**: A is creator but not owner.

The NFT is now permanently unburnable. The only escape is for B to voluntarily transfer the NFT back to A, which B can refuse. The same deadlock applies to `EditNFT`.

The test suite in `x/nft/keeper/keeper_test.go` explicitly documents this behavior and the required workaround (transfer back to creator before burning):

```go
// BurnNFT should fail when address is not the creator of denom
err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
// ...
// Transfer nft back to the creator of denom
err = suite.keeper.TransferOwner(suite.ctx, denomID, tokenID, address2, address)
// BurnNFT shouldn't fail when NFT exists and address is owner of nft and creator of denom
err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address)
```

### Impact Explanation

Any NFT minted to a recipient address that is not the denom creator is permanently unburnable and uneditable by anyone, unless the current owner voluntarily transfers it back to the creator. This is a permanent lock of NFT state reachable through standard production transactions. The NFT supply counter for the denom can never be decremented for such tokens, and the NFT record persists in chain state indefinitely.

**Corrupted invariant**: The NFT owner's right to burn their own token is permanently revoked the moment the token is transferred away from the denom creator. The denom creator's ability to manage their collection is also permanently blocked for any token not currently in their possession.

### Likelihood Explanation

Likelihood is **high**. The `MsgMintNFT` message explicitly accepts a `Recipient` field distinct from `Sender`, and the spec documents minting to a recipient as the standard use case. Any creator who mints NFTs to buyers, users, or any address other than themselves immediately triggers this condition for every minted token. This is the expected production flow.

### Recommendation

Separate the authorization logic so that either the NFT owner **or** the denom creator can burn/edit, rather than requiring both simultaneously. Alternatively, restrict `BurnNFT` to require only NFT ownership (the natural semantic of "burn what you own"), and restrict `EditNFT` to require only denom creator authority (the natural semantic of "the collection issuer controls metadata").

```go
// BurnNFT: require only ownership
func (k Keeper) BurnNFT(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error {
    nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
    if err != nil {
        return err
    }
    // Remove IsDenomCreator check
    k.deleteNFT(ctx, denomID, nft)
    k.deleteOwner(ctx, denomID, tokenID, owner)
    k.decreaseSupply(ctx, denomID)
    return nil
}
```

### Proof of Concept

Entry path (all standard Cosmos SDK transactions, no privileged keys required):

1. Alice sends `MsgIssueDenom{Id: "myclass", Sender: alice}` → Alice is denom creator.
2. Alice sends `MsgMintNFT{DenomId: "myclass", Id: "token1", Sender: alice, Recipient: bob}` → Bob owns `token1`.
3. Bob sends `MsgBurnNFT{DenomId: "myclass", Id: "token1", Sender: bob}` → **rejected**: `bob is not the creator of myclass`.
4. Alice sends `MsgBurnNFT{DenomId: "myclass", Id: "token1", Sender: alice}` → **rejected**: `alice is not the owner of myclass/token1`.
5. `token1` is now permanently unburnable. Neither the owner nor the creator can destroy it.

The same four-step sequence applies to `MsgEditNFT`.

**Root cause lines**: [1](#0-0) [2](#0-1) 

**Test confirming the deadlock**: [3](#0-2) 

**Spec documenting the dual-role requirement**: [4](#0-3)

### Citations

**File:** x/nft/keeper/keeper.go (L85-101)
```go
func (k Keeper) EditNFT(
	ctx sdk.Context, denomID, tokenID, tokenNm,
	tokenURI, tokenData string, owner sdk.AccAddress,
) error {
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

**File:** x/nft/keeper/keeper_test.go (L161-192)
```go
func (suite *KeeperSuite) TestBurnNFT() {
	// MintNFT should not fail when collection does not exist
	err := suite.keeper.MintNFT(suite.ctx, denomID, tokenID, tokenNm, tokenURI, tokenData, address, address)
	suite.NoError(err)

	// BurnNFT should fail when address is not the owner of nft
	err = suite.keeper.BurnNFT(suite.ctx, denomID, tokenID, address2)
	if suite.Error(err) {
		suite.EqualError(err, fmt.Sprintf("%s is not the owner of %s/%s: %s", address2, denomID, tokenID, types.ErrUnauthorized))
	}

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

	// NFT should no longer exist
	isNFT := suite.keeper.HasNFT(suite.ctx, denomID, tokenID)
	suite.False(isNFT)
```

**File:** x/nft/spec/02_messages.md (L109-127)
```markdown
### MsgBurnNFT

This message type is used for burning non-fungible tokens which destroys and deletes them. `Sender` of this message
should be the `Owner` of the NFT and `Creator` of the denomination corresponding to `DenomId`.

| **Field** | **Type** | **Description**                                    |
| :-------- | :------- | :------------------------------------------------- |
| Id        | `string` | The ID of the Token.                               |
| DenomId   | `string` | The Denom ID of the Token.                         |
| Sender    | `string` | The account address of the user burning the token. |

```go
// MsgBurnNFT defines an SDK message for burning a NFT.
type MsgBurnNFT struct {
    Id      string
    DenomId string
    Sender  string
}
```
```
