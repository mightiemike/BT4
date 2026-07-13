### Title
Missing Blocked-Address Guard in `MintNFT` Permanently Locks NFTs in Module Accounts — (`x/nft/keeper/keeper.go`, `x/nft/keeper/msg_server.go`)

---

### Summary

The NFT keeper's `MintNFT` → `MintNFTUnverified` path contains no check against blocked module account addresses. Any denom creator (an unprivileged role anyone can obtain) can call `MsgMintNFT` with `Recipient` set to a known module account address (e.g., `distribution`, `bonded_tokens_pool`). The NFT is then permanently locked with no on-chain recovery path.

---

### Finding Description

The full call chain for minting is:

```
MsgMintNFT (msg_server.go:54)
  → k.Keeper.MintNFT (keeper.go:72)
      → IsDenomCreator check only (keeper.go:76)
      → k.MintNFTUnverified (keeper.go:81)
          → k.setNFT (keeper.go:55)
          → k.setOwner (keeper.go:65)
          → k.increaseSupply (keeper.go:66)
```

**No guard at any layer checks whether the recipient is a blocked module account.**

`ValidateBasic` on `MsgMintNFT` only validates bech32 format and denom/token ID syntax: [1](#0-0) 

`msgServer.MintNFT` parses addresses and delegates directly to the keeper with no blocked-address check: [2](#0-1) 

`k.Keeper.MintNFT` only enforces `IsDenomCreator` before calling `MintNFTUnverified`: [3](#0-2) 

`MintNFTUnverified` only checks denom existence and NFT uniqueness, then writes state unconditionally: [4](#0-3) 

Critically, the `Keeper` struct holds only a `storeKey` and `cdc` — it has **no reference to an `AccountKeeper` with `BlockedAddr` capability**: [5](#0-4) 

The `expected_keepers.go` `AccountKeeper` interface exposes only `GetAccount` — no `BlockedAddr` method is defined or used anywhere in the NFT module: [6](#0-5) 

---

### Impact Explanation

Once an NFT is minted to a blocked module account (e.g., `distribution`), it is permanently irrecoverable:

- **`TransferNFT`** → `TransferOwner` → `IsOwner` check requires `sender == owner`. The module account is the owner but cannot sign a `MsgTransferNFT`. [7](#0-6) 

- **`BurnNFT`** requires the sender to be **both** the owner **and** the denom creator. The denom creator is not the owner (the module account is), and the module account cannot sign `MsgBurnNFT`. [8](#0-7) 

- There is no admin override, governance recovery, or module-level escape hatch in the NFT module.

The NFT is permanently locked. If it has economic value (e.g., it represents a game asset, a certificate, or an IBC-escrowed item), that value is destroyed with no recourse.

---

### Likelihood Explanation

- **Becoming a denom creator is permissionless**: any account can call `MsgIssueDenom`. [9](#0-8) 
- Module account addresses are **deterministic and publicly known** (derived from module name via `sdk.AccAddress(crypto.AddressHash([]byte(moduleName)))`). No privileged knowledge is required.
- The exploit requires only two transactions: `MsgIssueDenom` then `MsgMintNFT` with a module account as recipient. Both are standard, supported message types.

---

### Recommendation

Inject a `BlockedAddr` capability into the NFT keeper (via the bank keeper or account keeper) and add a guard in `MintNFTUnverified` (and `MintNFT`) before writing state:

```go
// In NewKeeper, accept a bankKeeper with BlockedAddr
if k.bankKeeper.BlockedAddr(owner) {
    return sdkerrors.Wrapf(types.ErrUnauthorized,
        "recipient %s is a blocked module account", owner)
}
```

This mirrors the standard Cosmos SDK bank module pattern. The `expected_keepers.go` `BankKeeper` interface should be extended to include `BlockedAddr(addr sdk.AccAddress) bool`.

---

### Proof of Concept

```go
// Keeper integration test (no mocks needed)
func TestMintNFTToBlockedModuleAccount(t *testing.T) {
    ctx, keeper := setupKeeper(t)

    // Step 1: Issue a denom (permissionless)
    creator := sdk.AccAddress([]byte("creator"))
    err := keeper.IssueDenom(ctx, "testdenom", "Test", "", "", creator)
    require.NoError(t, err)

    // Step 2: Mint NFT to the distribution module account address
    // cosmos1jv65s3grqf6v6jl3dp4t6c9t9rk99cd88lyufl (well-known, public)
    distModuleAddr := authtypes.NewModuleAddress(distrtypes.ModuleName)
    err = keeper.MintNFT(ctx, "testdenom", "token1", "Token1", "", "", creator, distModuleAddr)
    require.NoError(t, err) // succeeds — no blocked-address check

    // Step 3: Attempt TransferNFT from module account — fails, module cannot sign
    err = keeper.TransferOwner(ctx, "testdenom", "token1", distModuleAddr, creator)
    require.Error(t, err) // ErrUnauthorized: distModuleAddr is owner but cannot sign

    // Step 4: Attempt BurnNFT from denom creator — fails, creator is not owner
    err = keeper.BurnNFT(ctx, "testdenom", "token1", creator)
    require.Error(t, err) // ErrUnauthorized: creator != owner

    // NFT is now permanently locked with no recovery path.
}
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

**File:** x/nft/keeper/msg_server.go (L25-51)
```go
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
	}

	ctx.EventManager().EmitEvents(sdk.Events{
		sdk.NewEvent(
			types.EventTypeIssueDenom,
			sdk.NewAttribute(types.AttributeKeyDenomID, msg.Id),
			sdk.NewAttribute(types.AttributeKeyDenomName, msg.Name),
			sdk.NewAttribute(types.AttributeKeyCreator, msg.Sender),
		),
		sdk.NewEvent(
			sdk.EventTypeMessage,
			sdk.NewAttribute(sdk.AttributeKeyModule, types.AttributeValueCategory),
			sdk.NewAttribute(sdk.AttributeKeySender, msg.Sender),
		),
	})

	return &types.MsgIssueDenomResponse{}, nil
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

**File:** x/nft/keeper/keeper.go (L18-28)
```go
type Keeper struct {
	storeKey storetypes.StoreKey // Unexposed key to access store from sdk.Context
	cdc      codec.Codec
}

// NewKeeper creates a new instance of the NFT Keeper
func NewKeeper(cdc codec.Codec, storeKey storetypes.StoreKey) Keeper {
	return Keeper{
		storeKey: storeKey,
		cdc:      cdc,
	}
```

**File:** x/nft/keeper/keeper.go (L46-68)
```go
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

**File:** x/nft/keeper/keeper.go (L141-160)
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
```

**File:** x/nft/types/expected_keepers.go (L11-13)
```go
type AccountKeeper interface {
	GetAccount(ctx context.Context, addr sdk.AccAddress) sdk.AccountI
}
```
