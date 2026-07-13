### Title
ICS-721 `ValidateBasic` Enforces Bech32 on Source-Chain Sender Address, Permanently Blocking NFT Packets from Non-Bech32 Chains - (File: `x/nft-transfer/types/packet.go`)

---

### Summary

`NonFungibleTokenPacketData.ValidateBasic()` calls `sdk.AccAddressFromBech32` on the `Sender` field of an incoming IBC NFT packet. Because `Sender` is the address of the user on the **source chain** — which may use a non-bech32 format (e.g., an EVM hex address) — this check permanently rejects all ICS-721 packets originating from non-bech32 chains. The function's own comment explicitly states that address formats must not be validated here, making this a direct self-contradiction in the code.

---

### Finding Description

`NonFungibleTokenPacketData.ValidateBasic()` is annotated with:

> "NOTE: The addresses formats are not validated as the sender and recipient can have different formats defined by their corresponding chains that are not known to IBC."

Despite this, the function immediately calls `sdk.AccAddressFromBech32` on both `Sender` and `Receiver`: [1](#0-0) 

The `Sender` field in a received packet is the address of the user on the **source chain**. When chain-main acts as the destination, `ValidateBasic()` is invoked inside `Keeper.OnRecvPacket`: [2](#0-1) 

Which is called from `IBCModule.OnRecvPacket`: [3](#0-2) 

If the source chain uses EVM-style hex addresses (e.g., `0xF403C135812408BFbE8713b5A23a04b3D48AAE31`), `sdk.AccAddressFromBech32` returns an error at line 59 of `packet.go`, causing `OnRecvPacket` to return an error, which the IBC module converts to an error acknowledgement. The source chain then refunds the NFT via `refundPacketToken`, which correctly parses the sender as a bech32 address on the source chain side. The cross-chain transfer is permanently broken for any non-bech32 source chain. [4](#0-3) 

---

### Impact Explanation

Any IBC NFT transfer (ICS-721) from a chain using non-bech32 addresses (e.g., an EVM chain) to chain-main is permanently rejected at `OnRecvPacket`. The corrupted invariant is the ICS-721 protocol guarantee that any IBC-connected chain can transfer NFTs to chain-main regardless of its address format. The integration is completely broken for such counterparty chains — no NFT packet from a non-bech32 source will ever be accepted.

---

### Likelihood Explanation

The Cronos ecosystem explicitly targets EVM interoperability. Any relayer submitting a valid ICS-721 packet from an EVM chain (where `Sender` is a hex address) to chain-main triggers this unconditionally. No special attacker capability is required — a normal user initiating an NFT transfer from an EVM chain is sufficient. The entry path is: user submits IBC NFT transfer on EVM source chain → relayer submits `MsgRecvPacket` to chain-main → `OnRecvPacket` → `ValidateBasic()` → `sdk.AccAddressFromBech32(sender)` fails → error ack written → transfer permanently blocked.

---

### Recommendation

Remove the `sdk.AccAddressFromBech32` validation of `nftpd.Sender` from `ValidateBasic()` in `x/nft-transfer/types/packet.go`. Per the ICS-721 specification and the function's own comment, the sender address format is defined by the source chain and is not known to the receiving chain. Only a non-empty check is appropriate:

```go
if strings.TrimSpace(nftpd.Sender) == "" {
    return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "sender address cannot be blank")
}
// Remove: sdk.AccAddressFromBech32(nftpd.Sender) check
```

The `Receiver` bech32 validation in `ValidateBasic()` is redundant (it is already enforced in `processReceivedPacket` at line 143 of `packet.go`) and should also be removed from `ValidateBasic()` to keep the function spec-compliant.

---

### Proof of Concept

1. Deploy an IBC channel between chain-main and an EVM chain (e.g., Cronos EVM).
2. On the EVM chain, initiate an ICS-721 NFT transfer with sender `0xF403C135812408BFbE8713b5A23a04b3D48AAE31` and a valid chain-main bech32 receiver.
3. The relayer submits the packet to chain-main.
4. `IBCModule.OnRecvPacket` → `Keeper.OnRecvPacket` → `data.ValidateBasic()` → `sdk.AccAddressFromBech32("0xF403C135812408BFbE8713b5A23a04b3D48AAE31")` returns error `"invalid sender address"`.
5. An error acknowledgement is written; the NFT transfer is rejected.
6. The source chain processes the error ack and refunds the NFT to the sender.
7. No NFT packet from this EVM chain will ever succeed — the integration is permanently broken. [5](#0-4) [6](#0-5)

### Citations

**File:** x/nft-transfer/types/packet.go (L38-71)
```go
// ValidateBasic is used for validating the nft transfer.
// NOTE: The addresses formats are not validated as the sender and recipient can have different
// formats defined by their corresponding chains that are not known to IBC.
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

**File:** x/nft-transfer/ibc_module.go (L168-172)
```go
	if ack.Success() {
		if err := im.keeper.OnRecvPacket(ctx, channelVersion, packet, data); err != nil {
			ack = types.NewErrorAcknowledgement(err)
		}
	}
```
