### Title
Permanent NFT Loss on Sink-Chain Timeout/Ack-Error Refund via Different Channel — (`x/nft-transfer/keeper/packet.go`)

### Summary

When a sink-chain user sends an IBC NFT voucher back via a **different channel** than the one on which it was originally received, a timeout or ack-error triggers `refundPacketToken`, which calls `MintNFT` with the wrong escrow address as the `sender`. Because `MintNFT` enforces `IsDenomCreator`, and the denom creator is the escrow of the **original receive channel**, the refund fails permanently. The NFT was already burned in `BurnNFTUnverified` during `SendTransfer`, so the token is irrecoverably lost.

---

### Finding Description

**Step 1 — Chain B receives NFT from chain A on `channel-0`:**

In `processReceivedPacket`, the escrow address is derived from the **destination** port/channel:

```go
escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
// = GetEscrowAddress("nft", "channel-0") = escrow0
```

The denom is issued with `escrow0` as creator:

```go
k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress)
``` [1](#0-0) 

**Step 2 — Chain B user sends the voucher back via `channel-1`:**

`createOutgoingPacket` resolves `fullClassPath = "nft/channel-0/originalClassID"`. `IsAwayFromOrigin("nft", "channel-1", "nft/channel-0/...")` returns `false` (path does not start with `"nft/channel-1/"`), so the sink-chain branch fires:

```go
k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender)
``` [2](#0-1) 

The NFT is now burned. The packet's `ClassId` field is set to `fullClassPath = "nft/channel-0/originalClassID"`.

**Step 3 — Packet times out; `refundPacketToken` is called:**

```go
escrowAddress := types.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())
// = GetEscrowAddress("nft", "channel-1") = escrow1  ← WRONG
```

`isAwayFromOrigin` is again `false` (same `data.ClassId`), so the mint branch runs:

```go
k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender)
//                                                                            ^^^^^^^^^^^
//                                                                            escrow1, not escrow0
``` [3](#0-2) 

**Step 4 — `MintNFT` enforces `IsDenomCreator`:**

```go
func (k Keeper) MintNFT(..., sender, owner sdk.AccAddress) error {
    _, err := k.IsDenomCreator(ctx, denomID, sender)  // sender = escrow1
    if err != nil {
        return err  // "escrow1 is not the creator of voucherClassID"
    }
    ...
}
``` [4](#0-3) 

`IsDenomCreator` compares `denom.Creator` (= `escrow0`) against `escrow1`:

```go
if !creator.Equals(address) {
    return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
}
``` [5](#0-4) 

The refund returns an error. The NFT supply is permanently decremented with no recovery path.

---

### Impact Explanation

Any user on a sink chain who holds an IBC NFT voucher and attempts to send it back via any channel other than the exact channel it arrived on will permanently lose the NFT if the packet times out or receives an ack-error. The burned voucher cannot be re-minted because `refundPacketToken` uses the sending channel's escrow address as the minter, but the denom's creator is the receiving channel's escrow address. There is no fallback or retry mechanism.

---

### Likelihood Explanation

This is reachable by any user with a valid IBC NFT voucher on a sink chain that has more than one channel to the same or different counterparty. Multi-hop NFT routing (chain A → chain B → chain C, then chain B → chain C via a different channel) is a normal operational scenario. No special privileges are required; only a standard `MsgTransfer` on the wrong channel and a subsequent timeout.

---

### Recommendation

Replace `MintNFT` (which enforces `IsDenomCreator`) with `MintNFTUnverified` in `refundPacketToken`, mirroring how `BurnNFTUnverified` is used in `createOutgoingPacket`. The NFT keeper interface in `expected_keepers.go` must be extended to expose `MintNFTUnverified`. Alternatively, look up the actual denom creator from state and use that address as the `sender` argument to `MintNFT`. [6](#0-5) 

---

### Proof of Concept

```
1. Chain A issues denom "classA", mints tokenID "t1" to addr_A.
2. Chain A sends t1 to chain B via channel-0 (A-side) / channel-0 (B-side).
   → processReceivedPacket on B: IssueDenom(voucherClassID, creator=escrow0), MintNFT(t1, owner=addr_B).
3. addr_B calls SendTransfer(sourceChannel="channel-1", classID=voucherClassID, tokenID="t1").
   → IsAwayFromOrigin("nft","channel-1","nft/channel-0/classA") = false → BurnNFTUnverified(t1). NFT gone.
4. Packet times out (or counterparty returns ack-error).
   → refundPacketToken: escrowAddress=GetEscrowAddress("nft","channel-1")=escrow1.
   → MintNFT(voucherClassID, "t1", sender=escrow1, owner=addr_B).
   → IsDenomCreator(voucherClassID, escrow1) → error: escrow1 ≠ escrow0.
   → refundPacketToken returns error. NFT supply permanently -1. addr_B has no NFT.
```

Assert: after step 4, `GetNFT(voucherClassID, "t1")` returns not-found, and `GetTotalSupply(voucherClassID)` is 0 — confirming permanent burn with no refund.

### Citations

**File:** x/nft-transfer/keeper/packet.go (L33-48)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetSourcePort(), packet.GetSourceChannel())

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
```

**File:** x/nft-transfer/keeper/packet.go (L112-117)
```go
		} else {
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
		}
```

**File:** x/nft-transfer/keeper/packet.go (L151-168)
```go
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())

	if isAwayFromOrigin {
		// since SendPacket did not prefix the classID, we must prefix classID here
		classPrefix := types.GetClassPrefix(packet.GetDestPort(), packet.GetDestChannel())
		// NOTE: sourcePrefix contains the trailing "/"
		prefixedClassID := classPrefix + data.ClassId

		// construct the class trace from the full raw classID
		classTrace := types.ParseClassTrace(prefixedClassID)
		if !k.HasClassTrace(ctx, classTrace.Hash()) {
			k.SetClassTrace(ctx, classTrace)
		}

		voucherClassID := classTrace.IBCClassID()

		if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
			if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
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

**File:** x/nft/keeper/denom.go (L92-94)
```go
	if !creator.Equals(address) {
		return types.Denom{}, sdkerrors.Wrapf(types.ErrUnauthorized, "%s is not the creator of %s", address, denomID)
	}
```

**File:** x/nft-transfer/types/expected_keepers.go (L33-45)
```go
type NFTKeeper interface {
	HasDenomID(ctx sdk.Context, id string) bool
	GetDenom(ctx sdk.Context, id string) (denom nfttypes.Denom, err error)
	IssueDenom(ctx sdk.Context, id, name, schema, uri string, creator sdk.AccAddress) error

	GetNFT(ctx sdk.Context, denomID, tokenID string) (nft nftexported.NFT, err error)
	MintNFT(
		ctx sdk.Context, denomID, tokenID, tokenNm,
		tokenURI, tokenData string, sender, owner sdk.AccAddress,
	) error
	BurnNFTUnverified(ctx sdk.Context, denomID, tokenID string, owner sdk.AccAddress) error
	TransferOwner(ctx sdk.Context, denomID, tokenID string, srcOwner, dstOwner sdk.AccAddress) error
}
```
