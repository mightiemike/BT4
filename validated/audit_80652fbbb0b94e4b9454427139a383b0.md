### Title
IBC-Received NFT Denom Creator Incorrectly Set to Escrow Address, Permanently Blocking `MsgBurnNFT` and `MsgEditNFT` for All IBC NFT Holders - (`File: x/nft-transfer/keeper/packet.go`)

---

### Summary

When an IBC NFT packet is received and the voucher denom does not yet exist on the destination chain, `processReceivedPacket` creates the denom with the **channel escrow address** as the `creator`. Because `BurnNFT` and `EditNFT` in the NFT keeper require the caller to be **both** the NFT owner **and** the denom creator, every user who receives an IBC NFT is permanently blocked from burning or editing it via `MsgBurnNFT` / `MsgEditNFT`. No user can ever become the denom creator, and there is no mechanism to transfer denom ownership.

---

### Finding Description

In `processReceivedPacket`, when the voucher denom does not exist, it is created with `escrowAddress` as the creator:

```go
// x/nft-transfer/keeper/packet.go:167-171
if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
    if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
        return err
    }
}
``` [1](#0-0) 

The `escrowAddress` is a deterministic module-controlled address derived from the destination port and channel. No user can sign transactions as this address.

The NFT keeper's `BurnNFT` enforces a dual check — the caller must be both the NFT owner **and** the denom creator:

```go
// x/nft/keeper/keeper.go:146-154
nft, err := k.IsOwner(ctx, denomID, tokenID, owner)
if err != nil {
    return err
}
_, err = k.IsDenomCreator(ctx, denomID, owner)
if err != nil {
    return err
}
``` [2](#0-1) 

`EditNFT` applies the same dual check: [3](#0-2) 

Because the denom creator is `escrowAddress` and not the receiver, any user who holds an IBC-received NFT will always fail the `IsDenomCreator` check when submitting `MsgBurnNFT` or `MsgEditNFT`.

The IBC module itself bypasses this check by using `BurnNFTUnverified` (which skips the creator check) for its own internal operations: [4](#0-3) 

This confirms the designers were aware the creator check would block IBC operations — but the user-facing `MsgBurnNFT` path was not considered.

---

### Impact Explanation

Every user who receives an IBC NFT on this chain:

1. **Cannot burn their NFT** via `MsgBurnNFT` — `BurnNFT` calls `IsDenomCreator`, which returns `ErrUnauthorized` because the denom creator is `escrowAddress`, not the user.
2. **Cannot edit their NFT** via `MsgEditNFT` — `EditNFT` applies the same dual check.

The denom creator field is set once at creation and there is no `TransferDenomCreator` or equivalent function in the keeper. The lock is permanent. The only workaround — sending the NFT back via IBC — requires a live, open IBC channel to the source chain. If the channel is closed or the counterparty chain is unavailable, the user is permanently unable to burn the NFT they own.

The corrupted invariant is: **`Denom.Creator` for every IBC-received voucher denom is the escrow address, not any reachable user address**, making the `BurnNFT` and `EditNFT` paths permanently inaccessible to all NFT holders in those denoms.

---

### Likelihood Explanation

This affects **every first-time IBC NFT receive** on this chain. The code path is unconditional: whenever `processReceivedPacket` runs for a new `voucherClassID`, `escrowAddress` is passed as the creator. Any unprivileged user on a counterparty chain can trigger this by sending an IBC NFT transfer to this chain, and any user who receives such an NFT is immediately affected. No special conditions are required.

---

### Recommendation

Pass the `receiver` address as the denom creator instead of `escrowAddress`:

```diff
- if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
+ if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, receiver); err != nil {
```

This mirrors the fix in the referenced report: pass the actual end-user address (`receiver`) rather than the intermediary (`escrowAddress`). The IBC module's internal mint/burn operations already use `BurnNFTUnverified` and `MintNFT` with `escrowAddress` as the `sender` argument, so they will continue to work correctly regardless of who the denom creator is.

---

### Proof of Concept

1. Chain A holds a native NFT denom `myNFT` with token `token1`, owned by `alice`.
2. `alice` submits `MsgTransfer` on Chain A to send `token1` to `bob` on Chain B via IBC.
3. Chain B's `OnRecvPacket` → `processReceivedPacket` runs. The voucher denom `ibc/<hash>` does not exist yet, so `IssueDenom` is called with `escrowAddress` as creator. `token1` is minted to `bob`.
4. `bob` now owns `token1` in denom `ibc/<hash>` on Chain B.
5. `bob` submits `MsgBurnNFT{DenomId: "ibc/<hash>", Id: "token1", Sender: bob}` on Chain B.
6. The keeper calls `BurnNFT` → `IsDenomCreator(ctx, "ibc/<hash>", bob)` → returns `ErrUnauthorized: bob is not the creator of ibc/<hash>`.
7. The transaction fails. `bob` cannot burn the NFT he owns. The same failure occurs for `MsgEditNFT`. [5](#0-4) [6](#0-5)

### Citations

**File:** x/nft-transfer/keeper/packet.go (L113-116)
```go
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
```

**File:** x/nft-transfer/keeper/packet.go (L140-185)
```go
func (k Keeper) processReceivedPacket(ctx sdk.Context, packet channeltypes.Packet,
	data types.NonFungibleTokenPacketData,
) error {
	receiver, err := sdk.AccAddressFromBech32(data.Receiver)
	if err != nil {
		return err
	}

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)

	// create the escrow address for creating denom and minting nft
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
				return err
			}
		}
		sdkCtx := sdk.UnwrapSDKContext(ctx)
		sdkCtx.EventManager().EmitEvent(
			sdk.NewEvent(
				types.EventTypeClassTrace,
				sdk.NewAttribute(types.AttributeKeyTraceHash, classTrace.Hash().String()),
				sdk.NewAttribute(types.AttributeKeyClassID, voucherClassID),
			),
		)

		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
		}
```

**File:** x/nft/keeper/keeper.go (L93-101)
```go
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
