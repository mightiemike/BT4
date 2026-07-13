### Title
Default Case in `OnAcknowledgementPacket` Silently Drops Non-Error, Non-Result Acks Without Refund, Causing Permanent NFT Loss — (`x/nft-transfer/keeper/relay.go`)

---

### Summary

The keeper's `OnAcknowledgementPacket` switch statement only matches `*channeltypes.Acknowledgement_Error` to trigger `refundPacketToken`. Every other `ack.Response` value — including `nil` (produced when a counterparty writes a structurally valid but empty-response JSON ack such as `{}`) — falls through to `default: return nil`, permanently abandoning the escrowed or burned NFT with no refund and no error.

---

### Finding Description

In `x/nft-transfer/keeper/relay.go`:

```go
func (k Keeper) OnAcknowledgementPacket(..., ack channeltypes.Acknowledgement) error {
    switch ack.Response.(type) {
    case *channeltypes.Acknowledgement_Error:
        return k.refundPacketToken(ctx, packet, data)
    default:
        // comment claims "acknowledgement succeeded" — false for nil Response
        return nil
    }
}
``` [1](#0-0) 

The `default` branch conflates two distinct situations:
- `*channeltypes.Acknowledgement_Result` — a genuine success; returning `nil` is correct.
- `nil` Response — produced when `UnmarshalJSON` succeeds on a JSON object that contains neither `"result"` nor `"error"` (e.g., `{}`); returning `nil` silently drops the failure.

The entry point in `ibc_module.go` unmarshals the raw bytes and delegates to the keeper before doing any further validation:

```go
if err := types.ModuleCdc.UnmarshalJSON(acknowledgement, &ack); err != nil {
    return ...  // only rejects unparseable bytes
}
...
if err := im.keeper.OnAcknowledgementPacket(ctx, channelVersion, packet, data, ack); err != nil {
    return err
}
``` [2](#0-1) 

`UnmarshalJSON` on `{}` succeeds and leaves `ack.Response == nil`. IBC core does not inspect ack content — it only verifies the Merkle proof that the counterparty committed those bytes. Therefore a nil-Response ack passes all upstream guards and reaches the keeper switch.

---

### Impact Explanation

When the default case fires for a nil-Response ack:

- **Source-chain-is-origin path**: the NFT was escrowed to the module escrow address before the packet was sent. `refundPacketToken` is never called, so `TransferOwner` back to the sender never executes. The NFT remains locked in escrow forever with no owner able to recover it.
- **Source-chain-is-sink path**: the NFT was burned before the packet was sent. `refundPacketToken` is never called, so `MintNFT` back to the sender never executes. The NFT supply is permanently reduced. [3](#0-2) 

In both cases the sender loses the NFT with no recourse.

---

### Likelihood Explanation

Exploiting this requires a counterparty chain to commit an acknowledgement whose JSON deserializes to a `channeltypes.Acknowledgement` with a nil `Response` field. IBC core only verifies the Merkle proof; it does not validate ack content. A malicious or buggy counterparty chain can write `{}` (or any JSON that omits both `"result"` and `"error"` keys) as its ack. A relayer then submits `MsgAcknowledgement` to the sending chain, which passes IBC core validation and reaches this code path. No governance vote, no key compromise, and no local configuration change is required on the sending chain — only the counterparty's ack content needs to be crafted.

---

### Recommendation

Explicitly enumerate all known response types and treat anything else as an error:

```go
switch ack.Response.(type) {
case *channeltypes.Acknowledgement_Error:
    return k.refundPacketToken(ctx, packet, data)
case *channeltypes.Acknowledgement_Result:
    return nil
default:
    return sdkerrors.Wrapf(types.ErrInvalidAcknowledgement,
        "unknown acknowledgement response type: %T", ack.Response)
}
```

Returning an error from `OnAcknowledgementPacket` causes the IBC core to revert the transaction, preserving the packet commitment so the packet can be retried or timed out, rather than silently consuming it.

---

### Proof of Concept

```go
func TestOnAcknowledgementPacket_NilResponseDropsRefund(t *testing.T) {
    // Build a valid packet with an escrowed NFT
    packet := buildValidNFTPacket(...)

    // Construct an ack whose JSON has no "result" or "error" key
    // UnmarshalJSON succeeds; ack.Response == nil
    nilAck := channeltypes.Acknowledgement{} // Response field is nil

    err := keeper.OnAcknowledgementPacket(ctx, version, packet, data, nilAck)

    // Current code: err == nil, refundPacketToken never called
    // NFT remains escrowed; sender has lost it permanently
    require.Error(t, err, "expected error for unknown ack type, got nil")
    assertNFTReturnedToSender(t, ctx, data.Sender, data.TokenIds)
}
``` [4](#0-3)

### Citations

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

**File:** x/nft-transfer/ibc_module.go (L198-209)
```go
	var ack channeltypes.Acknowledgement
	if err := types.ModuleCdc.UnmarshalJSON(acknowledgement, &ack); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrUnknownRequest, "cannot unmarshal ICS-721 transfer packet acknowledgement: %v", err)
	}
	var data types.NonFungibleTokenPacketData
	if err := types.ModuleCdc.UnmarshalJSON(packet.GetData(), &data); err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrUnknownRequest, "cannot unmarshal ICS-721 transfer packet data: %s", err.Error())
	}

	if err := im.keeper.OnAcknowledgementPacket(ctx, channelVersion, packet, data, ack); err != nil {
		return err
	}
```

**File:** x/nft-transfer/keeper/packet.go (L35-49)
```go
	if isAwayFromOrigin {
		// unescrow tokens back to the sender
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx, voucherClassID, tokenID, escrowAddress, sender); err != nil {
				return err
			}
		}
	} else {
		// we are sink chain, mint voucher back to sender
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender); err != nil {
				return err
			}
		}
	}
```
