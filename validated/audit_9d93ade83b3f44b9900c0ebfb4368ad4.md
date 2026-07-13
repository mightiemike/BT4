### Title
Missing Blocked-Address Guard in `processReceivedPacket` Allows NFT Minting to Module Accounts — (`x/nft-transfer/keeper/packet.go`)

---

### Summary

`processReceivedPacket` decodes `data.Receiver` only as a valid bech32 address and immediately passes it to `MintNFT` or `TransferOwner`. There is no check against the chain's blocked-address set (module accounts). A counterparty chain can craft a packet whose `Receiver` is a blocked module account address (e.g., the `nft-transfer` module account, `bank` module account, etc.), causing the NFT to be permanently locked in an account that can never sign a transaction to move it.

---

### Finding Description

In `processReceivedPacket`, the receiver is resolved as:

```go
receiver, err := sdk.AccAddressFromBech32(data.Receiver)
if err != nil {
    return err
}
``` [1](#0-0) 

This is the **only** validation on the receiver address. After this, the code unconditionally calls either:

```go
k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver)
``` [2](#0-1) 

or:

```go
k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, receiver)
``` [3](#0-2) 

The `AccountKeeper` interface exposed to the nft-transfer module contains no `BlockedAddr` method: [4](#0-3) 

A search across the entire `x/nft-transfer/` tree confirms `BlockedAddr` / `IsBlockedAddr` is **never referenced** anywhere in the module. By contrast, `app.go` does configure blocked addresses for the bank module, but that protection is never wired into the nft-transfer path.

`ValidateBasic` on the packet data also performs no blocked-address check — it only validates non-empty bech32 format: [5](#0-4) 

The `OnRecvPacket` keeper entry point calls `data.ValidateBasic()` and then immediately delegates to `processReceivedPacket` with no further receiver validation: [6](#0-5) 

---

### Impact Explanation

A counterparty chain (or a user on it) sets `data.Receiver` to the bech32 address of any blocked module account on the destination chain (e.g., the `nft-transfer` module account itself, the `bank` module account, the `distribution` module account, etc.). The packet passes all validation, `processReceivedPacket` succeeds, and the NFT ownership record is updated to point to that module account. Because module accounts cannot sign transactions, the NFT can never be transferred away. The asset is permanently and irrecoverably lost. The exact state delta is: NFT owner field changes from `escrowAddress` → `moduleAccountAddress`, with no recovery path.

---

### Likelihood Explanation

The path is directly reachable via a standard IBC relayer submitting a `MsgRecvPacket` with attacker-controlled packet data. No governance, privileged role, or key compromise is required. Any user on a counterparty chain can set the `Receiver` field of their outgoing NFT transfer to a known module account address on the destination chain (module account addresses are deterministic and publicly derivable). This is the same class of bug that ICS-20 explicitly guards against with `BlockedAddr` checks in the bank module's `SendCoins`.

---

### Recommendation

Add a `BlockedAddr(addr sdk.AccAddress) bool` method to the `AccountKeeper` interface in `x/nft-transfer/types/expected_keepers.go`, and call it at the top of `processReceivedPacket` immediately after decoding the receiver:

```go
if k.authKeeper.BlockedAddr(receiver) {
    return newsdkerrors.Wrapf(sdkerrors.ErrUnauthorized,
        "receiver address %s is a blocked module account", data.Receiver)
}
```

This mirrors the protection present in the ICS-20 fungible token transfer module.

---

### Proof of Concept

1. Deploy a two-chain local testnet (chain A and chain B) with an IBC channel between them.
2. On chain A, mint an NFT and initiate an IBC transfer to chain B, setting `Receiver` to the bech32 address of chain B's `nft-transfer` module account (derivable via `types.GetEscrowAddress` or `authtypes.NewModuleAddress("nft-transfer")`).
3. Relay the packet to chain B.
4. Observe: `OnRecvPacket` returns a success acknowledgement, and the NFT ownership record on chain B is set to the `nft-transfer` module account address.
5. Attempt any transaction from the module account to transfer the NFT — it will be impossible because the module account has no private key.
6. Assert: the NFT is permanently locked with no recovery path.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L143-146)
```go
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
	if err != nil {
		return err
	}
```

**File:** x/nft-transfer/keeper/packet.go (L182-184)
```go
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L197-199)
```go
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
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

**File:** x/nft-transfer/types/packet.go (L41-71)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

	if len(nftpd.TokenIds) == 0 {
		return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
	}

	if len(nftpd.TokenIds) != len(nftpd.TokenUris) {
		return newsdkerrors.Wrap(ErrInvalidPacket, "tokenIds and tokenUris lengths do not match")
	}

	if strings.TrimSpace(nftpd.Sender) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "sender address cannot be blank")
	}

	// decode the sender address
	if _, err := sdk.AccAddressFromBech32(nftpd.Sender); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid sender address")
	}

	if strings.TrimSpace(nftpd.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "receiver address cannot be blank")
	}

	// decode the receiver address
	if _, err := sdk.AccAddressFromBech32(nftpd.Receiver); err != nil {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "invalid receiver address")
	}
	return nil
```

**File:** x/nft-transfer/keeper/relay.go (L101-111)
```go
func (k Keeper) OnRecvPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet,
	data types.NonFungibleTokenPacketData,
) error {
	// validate packet data upon receiving
	if err := data.ValidateBasic(); err != nil {
		return err
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	return k.processReceivedPacket(ctx, packet, data)
}
```
