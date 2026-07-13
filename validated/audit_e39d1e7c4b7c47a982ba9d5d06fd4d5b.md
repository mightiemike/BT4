### Title
Unguarded `MsgTransferNFT` Allows Direct Transfer to IBC Escrow Address, Permanently Locking NFTs — (`x/nft/keeper/msg_server.go`, `x/nft/keeper/keeper.go`)

---

### Summary

Any NFT owner can call `MsgTransferNFT` with `Recipient` set to the IBC nft-transfer escrow address (a module-derived, non-signing address). No guard in `ValidateBasic`, `TransferOwner`, or `swapOwner` prevents this. The NFT is permanently locked with no IBC packet to release it, violating the invariant that the escrow address's NFT holdings must correspond 1:1 with active in-flight IBC packets.

---

### Finding Description

**Entry point**: `msgServer.TransferNFT` in `x/nft/keeper/msg_server.go` [1](#0-0) 

`ValidateBasic` for `MsgTransferNFT` only validates bech32 format, denom ID, and token ID. There is no check that the recipient is not a module-derived or escrow address: [2](#0-1) 

`TransferOwner` in the keeper checks only that the denom exists and that `srcOwner` is the current owner. It places no restriction on `dstOwner`: [3](#0-2) 

`swapOwner` unconditionally deletes the old owner key and writes the new one: [4](#0-3) 

The NFT `Keeper` struct holds only `storeKey` and `cdc` — there is no `bankKeeper` or `accountKeeper` reference, so no blocked-address check is possible at the NFT layer: [5](#0-4) 

The escrow address is computed deterministically via ADR-028 and is registered as a plain account (not a named module account), so it does not appear in the bank module's blocked-address set: [6](#0-5) [7](#0-6) 

The legitimate IBC escrow path (`createOutgoingPacket`) calls `TransferOwner(sender, escrowAddress)` only after verifying the sender owns the NFT and after creating a real IBC packet: [8](#0-7) 

The unescrow path (`processReceivedPacket`, back-to-origin direction) calls `TransferOwner(escrowAddress, receiver)` for any tokenID present in the packet, with no check that the escrow address acquired that NFT via a legitimate IBC send: [9](#0-8) 

---

### Impact Explanation

1. **Permanent lock**: The attacker transfers their NFT to the escrow address via `MsgTransferNFT`. The escrow address cannot sign transactions, so no `MsgTransferNFT` can ever move the NFT out. No IBC packet exists to trigger `processReceivedPacket` or `refundPacketToken`. The NFT is irrecoverably lost.

2. **Invariant violation / cross-chain confusion**: The escrow address now holds an NFT with no corresponding in-flight IBC packet. If a future legitimate `OnRecvPacket` (back-to-origin) arrives for the same `(denomID, tokenID)`, `TransferOwner(escrowAddress, receiver)` will succeed and deliver the attacker-injected NFT to an unintended receiver, corrupting the IBC accounting.

3. **`GetTotalSupplyOfOwner` divergence**: `GetTotalSupplyOfOwner(escrowAddress)` will return a nonzero count with no matching packet in the channel store, breaking any invariant check that relies on this equality. [10](#0-9) 

---

### Likelihood Explanation

The attack requires only: (1) owning a valid NFT in any denom, (2) knowing the escrow address for any active IBC channel (publicly computable), and (3) submitting a standard `MsgTransferNFT`. No special privileges, governance, or operator access are needed. The path is fully reachable from a normal user transaction.

---

### Recommendation

Add a recipient blocklist check in `TransferOwner` (or in `msgServer.TransferNFT`) that rejects transfers to any known escrow address. Since the NFT keeper has no account keeper, the simplest fix is to add a callback or hook, or to pass a `blockedAddrs map[string]bool` into the NFT keeper at construction time (mirroring the pattern used by the bank module). Alternatively, add the check in `msgServer.TransferNFT` before calling `TransferOwner`, using the nft-transfer module's `GetEscrowAddress` for all active channels.

---

### Proof of Concept

```go
// keeper_test.go (new test)
func TestTransferToEscrowLocks(t *testing.T) {
    ctx, keeper := setupKeeper(t)

    // 1. Issue denom and mint NFT to attacker
    attacker := sdk.AccAddress([]byte("attacker__________"))
    _ = keeper.IssueDenom(ctx, "testdenom", "Test", "", "", attacker)
    _ = keeper.MintNFTUnverified(ctx, "testdenom", "token1", "", "", "", attacker)

    // 2. Compute escrow address for port "nft" / channel "channel-0"
    escrow := nfttransfertypes.GetEscrowAddress("nft", "channel-0")

    // 3. Transfer NFT directly to escrow via TransferOwner (same path as MsgTransferNFT)
    err := keeper.TransferOwner(ctx, "testdenom", "token1", attacker, escrow)
    require.NoError(t, err) // succeeds — no guard

    // 4. NFT is now owned by escrow with no IBC packet
    supply := keeper.GetTotalSupplyOfOwner(ctx, "testdenom", escrow)
    require.Equal(t, uint64(1), supply) // nonzero — invariant broken

    // 5. No IBC packet exists; NFT is permanently locked
    // escrow cannot sign MsgTransferNFT to recover it
}
```

### Citations

**File:** x/nft/keeper/msg_server.go (L129-146)
```go
func (m msgServer) TransferNFT(goCtx context.Context, msg *types.MsgTransferNFT) (*types.MsgTransferNFTResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	recipient, err := sdk.AccAddressFromBech32(msg.Recipient)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.TransferOwner(ctx, msg.DenomId, msg.Id,
		sender,
		recipient,
	); err != nil {
		return nil, err
	}
```

**File:** x/nft/types/msgs.go (L85-98)
```go
func (msg MsgTransferNFT) ValidateBasic() error {
	if err := ValidateDenomID(msg.DenomId); err != nil {
		return err
	}

	if _, err := sdk.AccAddressFromBech32(msg.Sender); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid sender address (%s)", err)
	}

	if _, err := sdk.AccAddressFromBech32(msg.Recipient); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "invalid recipient address (%s)", err)
	}
	return ValidateTokenID(msg.Id)
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

**File:** x/nft/keeper/owners.go (L95-101)
```go
func (k Keeper) swapOwner(ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress) {
	// delete old owner key
	k.deleteOwner(ctx, denomID, tokenID, srcOwner)

	// set new owner key
	k.setOwner(ctx, denomID, tokenID, dstOwner)
}
```

**File:** x/nft-transfer/types/keys.go (L45-56)
```go
func GetEscrowAddress(portID, channelID string) sdk.AccAddress {
	// a slash is used to create domain separation between port and channel identifiers to
	// prevent address collisions between escrow addresses created for different channels
	contents := fmt.Sprintf("%s/%s", portID, channelID)

	// ADR 028 AddressHash construction
	preImage := []byte(Version)
	preImage = append(preImage, 0)
	preImage = append(preImage, contents...)
	hash := sha256.Sum256(preImage)
	return hash[:20]
}
```

**File:** x/nft-transfer/keeper/keeper.go (L62-68)
```go
func (k Keeper) SetEscrowAddress(ctx sdk.Context, portID, channelID string) {
	// create the escrow address for the tokens
	escrowAddress := types.GetEscrowAddress(portID, channelID)
	if !k.authKeeper.HasAccount(ctx, escrowAddress) {
		acc := k.authKeeper.NewAccountWithAddress(ctx, escrowAddress)
		k.authKeeper.SetAccount(ctx, acc)
	}
```

**File:** x/nft-transfer/keeper/packet.go (L106-111)
```go
		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L196-201)
```go
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
		}
```

**File:** x/nft/keeper/collection.go (L110-118)
```go
func (k Keeper) GetTotalSupplyOfOwner(ctx sdk.Context, id string, owner sdk.AccAddress) (supply uint64) {
	store := ctx.KVStore(k.storeKey)
	iterator := storetypes.KVStorePrefixIterator(store, types.KeyOwner(owner, id, ""))
	defer iterator.Close()
	for ; iterator.Valid(); iterator.Next() {
		supply++
	}
	return supply
}
```
