### Title
Outdated ICS-721 Draft Packet Schema Causes Permanent NFT Escrow Lockup on Return Transfer — (`File: x/nft-transfer/types/packet.go`)

---

### Summary

`NonFungibleTokenPacketData` implements only the **draft** ICS-721 packet schema, which requires `token_uris` to be present and equal in length to `token_ids`. The **final** ICS-721 specification makes `token_uris` optional and introduces `token_data`. `ValidateBasic()` hard-rejects any packet where `len(tokenIds) != len(tokenUris)`. When a user sends an NFT from this chain to a counterparty implementing the final ICS-721 spec, the NFT is escrowed here. When the counterparty later returns the NFT using a final-spec packet (empty `token_uris`), this chain rejects it with an error acknowledgement, the counterparty refunds the voucher, and the original NFT remains permanently locked in the escrow account with no recovery path.

---

### Finding Description

`NonFungibleTokenPacketData` is defined in the proto schema and generated Go code as:

```
class_id, class_uri, token_ids[], token_uris[], sender, receiver
```

The final ICS-721 specification adds `class_data` and `token_data[]` and makes `token_uris` optional. This chain's implementation is missing those fields entirely.

The critical enforcement is in `ValidateBasic()`:

```go
// x/nft-transfer/types/packet.go:50-52
if len(nftpd.TokenIds) != len(nftpd.TokenUris) {
    return newsdkerrors.Wrap(ErrInvalidPacket, "tokenIds and tokenUris lengths do not match")
}
```

`ValidateBasic()` is called unconditionally inside `OnRecvPacket` keeper before any state mutation:

```go
// x/nft-transfer/keeper/relay.go:104-107
if err := data.ValidateBasic(); err != nil {
    return err
}
```

A final-spec counterparty chain that sends a return packet with `token_ids = ["X"]` and `token_uris = []` (using `token_data` instead) will always fail this check. The IBC module returns an error acknowledgement, the counterparty's `OnAcknowledgementPacket` calls `refundPacketToken` and re-mints the voucher to the user — but the original NFT on this chain remains in the escrow account created at:

```go
// x/nft-transfer/keeper/packet.go:108-110
escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
```

There is no governance or admin path to release an escrowed NFT. Every subsequent return-transfer attempt will fail identically, making the lockup permanent.

---

### Impact Explanation

- **Corrupted invariant**: The escrow account permanently holds the NFT. The original owner loses the asset with no on-chain recovery mechanism.
- **Scope**: Any NFT transferred from this chain to a counterparty implementing the final ICS-721 spec is at risk. The user on the counterparty chain retains a voucher that can never be redeemed, and the original NFT on this chain is irrecoverable.
- **No fund loss on counterparty**: The counterparty user keeps their voucher, but it is worthless because it can never be unwound back to the origin chain.

---

### Likelihood Explanation

ICS-721 is an active IBC standard. Multiple chains (e.g., those using the `ibc-go` nft-transfer module at v0.1.0+) implement the final spec with optional `token_uris`. Any cross-chain NFT round-trip between this chain and such a counterparty triggers the lockup. The trigger is a standard, unprivileged `MsgTransfer` transaction — no special role or key is required.

---

### Recommendation

1. Add `class_data` and `token_data` fields to `NonFungibleTokenPacketData` to match the final ICS-721 spec.
2. Remove the strict length-equality check between `token_ids` and `token_uris` in `ValidateBasic()`, or make it conditional on `token_uris` being non-empty.
3. Update `processReceivedPacket` and `refundPacketToken` to handle the case where `token_uris` is empty and `token_data` carries the metadata.
4. Support both draft and final ICS-721 packet formats during a transition period (analogous to the ERC721 fix that supported both `ERC721ReceiverFinal` and `ERC721ReceiverDraft`).

---

### Proof of Concept

**Step 1**: User on Chain A (this chain) calls `MsgTransfer` for NFT `tokenX` in class `nftClass` to a receiver on Chain B (final ICS-721 chain).

`createOutgoingPacket` escrows `tokenX` to `GetEscrowAddress("nft", "channel-0")` and sends a packet with `token_uris = ["https://..."]`. [1](#0-0) 

**Step 2**: Chain B accepts the packet (it has `token_uris` populated), mints a voucher `nft/channel-1/nftClass::tokenX` to the receiver.

**Step 3**: Receiver on Chain B calls their chain's `MsgTransfer` to return `tokenX` to Chain A. Chain B, implementing the final ICS-721 spec, sends a packet with `token_ids = ["tokenX"]`, `token_uris = []`, `token_data = ["<metadata>"]`.

**Step 4**: Chain A's `OnRecvPacket` deserializes the packet. `token_uris` deserializes as an empty slice (unknown field `token_data` is silently skipped by protobuf). `ValidateBasic()` is called:

```
len(tokenIds)=1 != len(tokenUris)=0  →  ErrInvalidPacket
``` [2](#0-1) 

**Step 5**: Chain A returns an error acknowledgement. Chain B's `OnAcknowledgementPacket` calls `refundPacketToken`, re-minting the voucher to the sender on Chain B. [3](#0-2) 

**Step 6**: `tokenX` remains owned by `GetEscrowAddress("nft", "channel-0")` on Chain A permanently. No governance message, no admin key, and no IBC path can release it. Every retry of Step 3 produces the same rejection. [4](#0-3)

### Citations

**File:** x/nft-transfer/keeper/packet.go (L33-41)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())

	if isAwayFromOrigin {
		// unescrow tokens back to the sender
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, sender); err != nil {
				return err
			}
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

**File:** x/nft-transfer/types/packet.go (L50-52)
```go
	if len(nftpd.TokenIds) != len(nftpd.TokenUris) {
		return newsdkerrors.Wrap(ErrInvalidPacket, "tokenIds and tokenUris lengths do not match")
	}
```

**File:** x/nft-transfer/keeper/relay.go (L117-126)
```go
func (k Keeper) OnAcknowledgementPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet, data types.NonFungibleTokenPacketData, ack channeltypes.Acknowledgement) error {
	switch ack.Response.(type) {
	case *channeltypes.Acknowledgement_Error:
		return k.refundPacketToken(ctx, packet, data)
	default:
		// the acknowledgement succeeded on the receiving chain so nothing
		// needs to be executed and no error needs to be returned
		return nil
	}
}
```
