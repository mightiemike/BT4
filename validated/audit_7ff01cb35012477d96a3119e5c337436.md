### Title
IBC NFT Receive Mints to Blocked Module Accounts Without Guard — (`x/nft-transfer/keeper/packet.go`)

### Summary

The `processReceivedPacket` function in the nft-transfer module mints IBC NFT vouchers directly to the `receiver` address decoded from the packet data, with no check that the receiver is not a blocked module account. This allows any sender on a source chain to permanently strand an NFT in a module account on the destination chain, incrementing the supply counter while the NFT becomes irrecoverable.

### Finding Description

In `processReceivedPacket`, the receiver is parsed from the packet data and passed directly as the `owner` to `MintNFT`: [1](#0-0) [2](#0-1) 

`ValidateBasic()` on the packet data only validates that the receiver is a non-empty, valid bech32 address — it does not check whether the address is a blocked module account: [3](#0-2) 

The `AccountKeeper` interface exposed to the nft-transfer keeper has no `BlockedAddr` method, and no such check exists anywhere in the module: [4](#0-3) 

`MintNFT` in the nft keeper checks only that the `sender` (the IBC escrow address) is the denom creator, then delegates to `MintNFTUnverified` with `owner=receiver` unchecked: [5](#0-4) 

`MintNFTUnverified` performs no blocked-address validation — it sets the NFT owner, records ownership, and increments supply unconditionally: [6](#0-5) 

The same gap applies to the `TransferOwner` path (back-to-origin direction): [7](#0-6) 

### Impact Explanation

A sender on the source chain specifies a blocked module account address (e.g., the distribution or bonded-pool module account) as the `receiver` in the IBC NFT transfer packet. The relayer relays the packet. `OnRecvPacket` → `processReceivedPacket` → `MintNFT` succeeds, recording the module account as the NFT owner and incrementing the denom supply counter. Since module accounts have no signing key, no transaction can ever call `TransferOwner`, `BurnNFT`, or `EditNFT` on behalf of that account. The NFT is permanently inaccessible while the supply counter remains inflated — a persistent supply/ownership state inconsistency.

### Likelihood Explanation

The path is fully reachable by any user who initiates an IBC NFT transfer on a source chain with `receiver` set to a known module account address on the destination chain. No privileged role, governance action, or key compromise is required. The relayer is passive. The Cosmos SDK's ICS-20 fungible token transfer module explicitly guards against this with `BlockedAddr`; the ICS-721 implementation here omits the equivalent check entirely.

### Recommendation

Add a `BlockedAddr(addr sdk.AccAddress) bool` method to the `AccountKeeper` interface in `x/nft-transfer/types/expected_keepers.go`, wire it to the bank keeper's implementation in `NewKeeper`, and reject packets in `processReceivedPacket` when `k.authKeeper.BlockedAddr(receiver)` returns true — mirroring the guard present in the Cosmos SDK ICS-20 transfer module.

### Proof of Concept

1. On the source chain, call `MsgTransfer` with `receiver = sdk.AccAddress(authtypes.NewModuleAddress("distribution")).String()`.
2. The IBC relayer relays the resulting packet to the destination chain.
3. `OnRecvPacket` → `processReceivedPacket` → `MintNFT(escrowAddress, distributionModuleAddr)` succeeds with a success ACK.
4. Query the NFT: owner is the distribution module account. Query denom supply: incremented by 1.
5. Attempt any operation (transfer, burn, edit) on behalf of the distribution module account: all fail — no signing key exists.
6. The NFT is permanently stranded; supply counter is permanently inflated.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L143-143)
```go
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
```

**File:** x/nft-transfer/keeper/packet.go (L181-184)
```go
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L196-200)
```go
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
```

**File:** x/nft-transfer/types/packet.go (L63-71)
```go
	if strings.TrimSpace(nftpd.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "receiver address cannot be blank")
	}

	// decode the receiver address
	if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
	}
	return nil
```

**File:** x/nft-transfer/types/expected_keepers.go (L47-54)
```go
// AccountKeeper defines the contract required for account APIs.
type AccountKeeper interface {
	NewAccountWithAddress(ctx context.Context, addr sdk.AccAddress) sdk.AccountI
	// Set an account in the store.
	GetAccount(ctx context.Context, addr sdk.AccAddress) sdk.AccountI
	HasAccount(ctx context.Context, addr sdk.AccAddress) bool
	SetAccount(ctx context.Context, acc sdk.AccountI)
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

**File:** x/nft/keeper/keeper.go (L71-82)
```go
// MintNFT mints an NFT and manages the NFT's existence within Collections and Owners
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
