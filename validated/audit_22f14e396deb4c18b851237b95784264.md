### Title
NFT Permanently Locked in Escrow via Receiver Set to Escrow Address on Return Transfer — (`x/nft-transfer/keeper/packet.go`)

### Summary
`processReceivedPacket` performs no check that the `receiver` address is not the escrow address itself. An attacker holding an IBC voucher NFT (sink chain, `isAwayFromOrigin=false`) can set `receiver = GetEscrowAddress(destPort, destChannel)` on the origin chain, causing `TransferOwner` to be called with `srcOwner == dstOwner == escrowAddress`. The NFT stays permanently owned by the escrow address with no user able to claim it.

### Finding Description

**Entrypoint:** `MsgTransfer` → `SendTransfer` → `createOutgoingPacket` → IBC relay → `OnRecvPacket` → `processReceivedPacket`

**Setup:**
- Chain A: origin, holds native NFT `nftClass/tokenID` escrowed at `GetEscrowAddress("nft", "channel-Y")`
- Chain B: sink, attacker holds voucher `ibc/<hash>/tokenID` (full path: `nft/channel-X/nftClass`)

**Attack trace:**

**Step 1 — Chain B (`createOutgoingPacket`):**

`IsAwayFromOrigin("nft", "channel-X", "nft/channel-X/nftClass")` returns `false` because the full class path has the source port/channel as its prefix. [1](#0-0) 

So the `else` branch fires and `BurnNFTUnverified` destroys the voucher. [2](#0-1) 

The packet is constructed with `receiver = GetEscrowAddress("nft", "channel-Y").String()` — the escrow address on chain A. No validation rejects this. [3](#0-2) 

**Step 2 — `MsgTransfer.ValidateBasic` / `NonFungibleTokenPacketData.ValidateBasic`:**

Neither validates that `receiver` is not an escrow address. Both only check non-empty string and valid bech32. [4](#0-3) [5](#0-4) 

**Step 3 — Chain A (`processReceivedPacket`):**

`isAwayFromOrigin` is again `false`. The escrow address is computed as:
```
escrowAddress = GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
             = GetEscrowAddress("nft", "channel-Y")
``` [6](#0-5) 

`receiver` is parsed from `data.Receiver` — which the attacker set to exactly `GetEscrowAddress("nft", "channel-Y")`. So `receiver == escrowAddress`.

The unescrow call becomes:
```go
TransferOwner(ctx, "nftClass", tokenID, escrowAddress, receiver)
// where escrowAddress == receiver
``` [7](#0-6) 

**Step 4 — `TransferOwner` with identical src/dst:**

`TransferOwner` has no guard against `srcOwner == dstOwner`. It calls `IsOwner` (passes, since escrow owns the NFT), sets `nft.Owner = dstOwner.String()` (no change), and calls `swapOwner(escrowAddress, escrowAddress)`. [8](#0-7) 

The NFT remains owned by the escrow address. No user-controlled address ever receives it. The escrow address has no private key; the NFT is permanently inaccessible.

### Impact Explanation

The original NFT on chain A is permanently locked in the escrow address. The escrow address is a deterministic hash-derived address with no corresponding private key. [9](#0-8) 

The invariant broken: on a return transfer (`isAwayFromOrigin=false`), the unescrow operation must deliver the NFT to a user-controlled address. Instead, it delivers to the escrow address itself — a permanent lock.

### Likelihood Explanation

- Attacker must hold a voucher NFT on the sink chain (normal user scenario).
- Attacker sacrifices their voucher to permanently destroy the original NFT's accessibility.
- The escrow address is publicly queryable via the `EscrowAddress` gRPC/CLI query, so the attacker can trivially compute the target address.
- No privileged role, governance, or operator compromise required.
- Exploitable by any voucher holder against any NFT that has been IBC-transferred.

### Recommendation

In `processReceivedPacket`, add a guard before calling `TransferOwner`:

```go
if receiver.Equals(escrowAddress) {
    return sdkerrors.Wrap(types.ErrInvalidReceiver,
        "receiver cannot be the escrow address")
}
```

Additionally, `MsgTransfer.ValidateBasic` cannot easily enumerate all escrow addresses, but the keeper-level check in `processReceivedPacket` is sufficient and is the correct enforcement point.

### Proof of Concept

```
// Chain A: native NFT "nftClass/token1" escrowed at escrowA = GetEscrowAddress("nft","channel-Y")
// Chain B: attacker holds voucher ibc/<hash>/token1

// 1. Attacker queries escrow address on chain A
escrowA := types.GetEscrowAddress("nft", "channel-Y")

// 2. Attacker submits MsgTransfer on chain B
msg := types.NewMsgTransfer(
    "nft", "channel-X",
    "ibc/<hash>", []string{"token1"},
    attacker.String(),
    escrowA.String(),   // <-- receiver = escrow address on chain A
    timeoutHeight, 0,
)
// BurnNFTUnverified fires on chain B; voucher is destroyed

// 3. Relay packet to chain A
// processReceivedPacket: isAwayFromOrigin=false
// escrowAddress = GetEscrowAddress("nft","channel-Y") == receiver
// TransferOwner(ctx, "nftClass", "token1", escrowAddress, escrowAddress)
// NFT.Owner remains escrowA.String() — permanently locked

// 4. Assert
nft, _ := nftKeeper.GetNFT(ctx, "nftClass", "token1")
assert(nft.GetOwner().Equals(escrowA))  // true — locked forever
```

### Citations

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
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

**File:** x/nft-transfer/keeper/packet.go (L120-122)
```go
	packetData := types.NewNonFungibleTokenPacketData(
		fullClassPath, denom.Uri, tokenIDs, tokenURIs, sender.String(), receiver,
	)
```

**File:** x/nft-transfer/keeper/packet.go (L148-151)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)

	// create the escrow address for creating denom and minting nft
	escrowAddress := types.GetEscrowAddress(packet.GetDestPort(), packet.GetDestChannel())
```

**File:** x/nft-transfer/keeper/packet.go (L196-200)
```go
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
```

**File:** x/nft-transfer/types/msgs.go (L84-87)
```go
	if strings.TrimSpace(msg.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "missing recipient address")
	}
	return nil
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

**File:** x/nft-transfer/types/keys.go (L45-55)
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
```
