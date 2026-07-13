The code trace is complete. Here is the full analysis:

---

### Title
Authz Generic Grant for `MsgMintNFT` Allows Grantee to Mint NFTs to Themselves — (`x/nft/keeper/keeper.go`, `x/nft/types/msgs.go`)

### Summary

`MsgMintNFT.GetSigners()` returns only `msg.Sender` (the denom creator / granter). The `Recipient` field is a separate, independently settable field with no authz-level restriction. A grantee holding a generic authz grant for `MsgMintNFT` can wrap the message in `MsgExec` with `Recipient` set to their own address, causing the keeper to mint an NFT into the attacker's account while the `IsDenomCreator` check passes against the granter.

### Finding Description

**Step 1 — `GetSigners` only covers `Sender`:**

`MsgMintNFT.GetSigners()` returns `[]sdk.AccAddress{from}` where `from` is parsed from `msg.Sender`. [1](#0-0) 

The `Recipient` field is validated only for address format in `ValidateBasic`, not for authorization. [2](#0-1) 

**Step 2 — authz `MsgExec` flow:**

The Cosmos SDK authz module checks that the signer of the inner message (i.e., `GetSigners()[0]` = `msg.Sender` = granter) has granted the grantee permission to execute the message type. With a generic grant for `MsgMintNFT`, this check passes unconditionally for any `MsgMintNFT` regardless of the `Recipient` value. The grantee constructs:

```
MsgMintNFT{
  Sender:    granter_address,   // denom creator
  Recipient: attacker_address,  // freely chosen by grantee
  DenomId:   granter_denom,
  Id:        new_token_id,
}
```

**Step 3 — `msgServer.MintNFT` passes both addresses to the keeper:**

The server parses `recipient` from `msg.Recipient` and `sender` from `msg.Sender` independently, then forwards both to the keeper. [3](#0-2) 

**Step 4 — Keeper `MintNFT` checks only `IsDenomCreator(sender)`:**

The keeper verifies that `sender` (= granter) is the denom creator. This check passes. It then calls `MintNFTUnverified` with `owner = recipient` (= attacker). [4](#0-3) 

**Step 5 — `MintNFTUnverified` writes the NFT to the attacker's address:**

`MintNFTUnverified` stores the NFT with `owner = attacker` and updates the owner index accordingly, with no further authorization check. [5](#0-4) 

**Step 6 — `IsDenomCreator` only checks `sender`, never `recipient`:**

The denom creator guard is entirely on the `sender` path; `recipient` is never validated against any authorization constraint. [6](#0-5) 

### Impact Explanation

An authz grantee can mint an arbitrary number of NFTs (subject only to unique token IDs) into their own account within the granter's denom, without the granter's per-mint consent. Each minted NFT is a real on-chain asset in the granter's collection, increasing the attacker's NFT balance and the denom's total supply. The granter loses exclusive control over who receives newly minted NFTs in their denom.

### Likelihood Explanation

Generic authz grants for `MsgMintNFT` are a natural use case: a denom creator might delegate minting rights to a marketplace contract, a backend service, or a co-creator. Any such grantee can immediately exploit this to self-mint. No additional privileges, governance actions, or key compromise are required beyond the grant itself.

### Recommendation

In `keeper.MintNFT` (or in `msgServer.MintNFT`), add a check that `recipient` equals `sender` **or** that the `sender` has explicitly authorized the specific `recipient`. Alternatively, introduce a `MsgMintNFTAuthorization` authz type (analogous to `SendAuthorization`) that restricts the allowed `Recipient` addresses, so generic grants cannot be used to mint to arbitrary recipients.

### Proof of Concept

```go
// 1. Granter issues denom and grants generic authz for MsgMintNFT to attacker
granterAddr := sdk.AccAddress(...)
attackerAddr := sdk.AccAddress(...)

// IssueDenom by granter
_ = nftKeeper.IssueDenom(ctx, "testdenom", "Test", "", "", granterAddr)

// Grant generic authz
grant, _ := authz.NewGrant(ctx.BlockTime(), authz.NewGenericAuthorization(sdk.MsgTypeURL(&nfttypes.MsgMintNFT{})), nil)
authzKeeper.SaveGrant(ctx, attackerAddr, granterAddr, grant)

// 2. Attacker wraps MsgMintNFT with Recipient = attacker
innerMsg := &nfttypes.MsgMintNFT{
    Id:        "token1",
    DenomId:   "testdenom",
    Sender:    granterAddr.String(),   // granter is Sender → passes IsDenomCreator
    Recipient: attackerAddr.String(),  // attacker sets themselves as recipient
}
execMsg := authz.NewMsgExec(attackerAddr, []sdk.Msg{innerMsg})

// 3. Execute — succeeds, NFT minted to attacker
_, err := authzMsgServer.Exec(ctx, &execMsg)
assert.NoError(t, err)

// 4. Assert attacker owns the NFT
supply := nftKeeper.GetTotalSupplyOfOwner(ctx, "testdenom", attackerAddr)
assert.Equal(t, uint64(1), supply) // attacker received NFT without granter's per-mint consent
```

### Citations

**File:** x/nft/types/msgs.go (L176-190)
```go
func (msg MsgMintNFT) ValidateBasic() error {
	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}
	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid receipt address (%s)", err)
	}
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}
	if err := ValidateTokenURI(msg.URI); err != nil {
		return err
	}
	return ValidateTokenID(msg.Id)
}
```

**File:** x/nft/types/msgs.go (L193-199)
```go
func (msg MsgMintNFT) GetSigners() []sdk.AccAddress {
	from, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		panic(err)
	}
	return []sdk.AccAddress{from}
}
```

**File:** x/nft/keeper/msg_server.go (L54-74)
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
		return nil, err
	}
```

**File:** x/nft/keeper/keeper.go (L44-68)
```go
// MintNFTUnverified mints an NFT without verifying if the owner is the creator of denom
// Needed during genesis initialization
func (k Keeper) MintNFTUnverified(ctx sdk.Context, denomID, tokenID, tokenNm, tokenURI, tokenData string, owner sdk.AccAddress) error {
	if !k.HasDenomID(ctx, denomID) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denom ID %s not exists", denomID)
	}

	if k.HasNFT(ctx, denomID, tokenID) {
		return sdkerrors.Wrapf(types.ErrNFTAlreadyExists, "NFT %s already exists in collection %s", tokenID, denomID)
	}

	k.setNFT(
		ctx, denomID,
		types.NewBaseNFT(
			tokenID,
			tokenNm,
			owner,
			tokenURI,
			tokenData,
		),
	)
	k.setOwner(ctx, denomID, tokenID, owner)
	k.increaseSupply(ctx, denomID)

	return nil
```

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

**File:** x/nft/keeper/denom.go (L81-96)
```go
func (k Keeper) IsDenomCreator(ctx sdk.Context, denomID string, address sdk.AccAddress) (types.Denom, error) {
	denom, err := k.GetDenom(ctx, denomID)
	if err != nil {
		return types.Denom{}, err
	}

	creator, err := sdk.AccAddressFromBech32(denom.Creator)
	if err != nil {
		panic(err)
	}

	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}

	return denom, nil
```
