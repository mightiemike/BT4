### Title
IBC NFT Receive Accepts `ibc/<hash>` ClassId, Creating Unredeemable Phantom Voucher Class — (`x/nft-transfer/keeper/packet.go`, `x/nft-transfer/types/trace.go`)

---

### Summary

`processReceivedPacket` calls `IsAwayFromOrigin` with the raw `data.ClassId` from the packet. When a malicious counterparty sends `ClassId='ibc/ABCD...'` (the local IBC-hashed form), the prefix check fails, `isAwayFromOrigin=true` is returned, and the code prepends the destination port/channel prefix, producing a double-prefixed path `destPort/destChannel/ibc/ABCD...`. This is stored as a class trace with a structurally invalid path and hashed to a new phantom voucher class ID. NFTs are minted under this phantom class and can never be redeemed.

---

### Finding Description

**Step 1 — Entry point: `OnRecvPacket`**

`OnRecvPacket` calls `data.ValidateBasic()` before delegating to `processReceivedPacket`. [1](#0-0) 

`ValidateBasic` only rejects a blank `ClassId`; it does not reject the `ibc/<hash>` format: [2](#0-1) 

So `ClassId='ibc/ABCD...'` passes validation.

**Step 2 — `IsAwayFromOrigin` misclassifies the direction**

`processReceivedPacket` calls `IsAwayFromOrigin` with the raw `data.ClassId`: [3](#0-2) 

`IsAwayFromOrigin` checks `strings.HasPrefix(fullClassPath, sourcePort+"/"+sourceChannel+"/")`: [4](#0-3) 

`'ibc/ABCD...'` does not start with `'nft/channel-X/'`, so the function returns `true`.

**Step 3 — Double-prefixing**

Because `isAwayFromOrigin=true`, the code prepends the destination prefix: [5](#0-4) 

This produces `prefixedClassID = 'nft/destChannel/ibc/ABCD...'`.

**Step 4 — `ParseClassTrace` produces a structurally invalid trace**

`ParseClassTrace` splits on `/` and takes everything except the last segment as the path: [6](#0-5) 

For `'nft/destChannel/ibc/ABCD...'`:
- `Path = 'nft/destChannel/ibc'` (3 identifiers — an odd number, violating the port/channel pair invariant)
- `BaseClassId = 'ABCD...'`

`Validate()` would catch this via `validateTraceIdentifiers` (requires even-length pairs): [7](#0-6) 

But `Validate()` is **never called** in the receive path. The trace is stored directly: [8](#0-7) 

**Step 5 — Phantom voucher class ID is created and NFTs are minted**

`IBCClassID()` hashes the malformed path to produce `ibc/<sha256('nft/destChannel/ibc/ABCD...')>`: [9](#0-8) 

A new denom is issued and NFTs are minted under this phantom class ID: [10](#0-9) 

**Step 6 — Redemption is impossible**

The stored class trace `{Path:'nft/destChannel/ibc', BaseClassId:'ABCD...'}` has a malformed path. Any attempt to send these NFTs back would reconstruct the full path as `'nft/destChannel/ibc/ABCD...'`, strip the `nft/destChannel/` prefix, and present `'ibc/ABCD...'` to the counterparty — which never escrowed anything under that identifier in a recoverable way. The phantom voucher class has no legitimate backing and no valid redemption path.

---

### Impact Explanation

A malicious IBC counterparty chain can cause the receiving chain to:
1. Store a structurally invalid class trace (odd-length path) in the class trace store.
2. Issue a phantom NFT denom (`ibc/<H2>`) that has no canonical backing.
3. Mint NFTs under this phantom denom for the receiver.

The NFTs are permanently unredeemable. Any user who receives them holds assets with no valid return path. The original NFTs on the counterparty chain may be escrowed or burned at the attacker's discretion. This breaks the invariant that each NFT class has exactly one canonical IBC class ID per hop and corrupts the NFT ownership/escrow state.

---

### Likelihood Explanation

This requires a malicious IBC counterparty chain — one that deliberately sends `ClassId='ibc/<hash>'` instead of the full class path. A well-behaved chain using this same codebase would never do this (`createOutgoingPacket` converts `ibc/<hash>` to the full path before packing). However, IBC security explicitly does not assume counterparty chain correctness; any chain with an established channel can craft arbitrary packet data. The attack is trivially constructable and requires no special privileges beyond having an open IBC channel.

---

### Recommendation

In `OnRecvPacket` (or `processReceivedPacket`), reject any incoming packet whose `ClassId` starts with the `ClassPrefix` (`"ibc"`):

```go
// in ValidateBasic or at the top of processReceivedPacket:
if strings.HasPrefix(data.ClassId, types.ClassPrefix+"/") {
    return sdkerrors.Wrapf(types.ErrInvalidClassID,
        "classId cannot be IBC-prefixed in packet data: %s", data.ClassId)
}
```

Additionally, call `classTrace.Validate()` after `ParseClassTrace` in `processReceivedPacket` before storing the trace, so structurally invalid paths (odd-length identifier lists) are caught at the boundary.

---

### Proof of Concept

```go
// Pseudocode unit test for processReceivedPacket
packet := channeltypes.Packet{
    SourcePort:      "nft",
    SourceChannel:   "channel-0",
    DestinationPort: "nft",
    DestinationChannel: "channel-1",
}
data := types.NonFungibleTokenPacketData{
    ClassId:   "ibc/ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890ABCDEF1234567890",
    TokenIds:  []string{"token1"},
    TokenUris: []string{"uri1"},
    Sender:    "<valid bech32>",
    Receiver:  "<valid bech32>",
}

// ValidateBasic passes — ClassId is not blank
assert(data.ValidateBasic() == nil)

// IsAwayFromOrigin returns true — "ibc/ABCD..." does not start with "nft/channel-0/"
assert(types.IsAwayFromOrigin("nft", "channel-0", data.ClassId) == true)

// prefixedClassID = "nft/channel-1/ibc/ABCDEF..."
prefixed := "nft/channel-1/" + data.ClassId
ct := types.ParseClassTrace(prefixed)
// ct.Path = "nft/channel-1/ibc"  (3 identifiers — INVALID)
// ct.BaseClassId = "ABCDEF..."

// IBCClassID hashes the malformed path
phantomID := ct.IBCClassID()  // "ibc/<sha256(nft/channel-1/ibc/ABCDEF...)>"

// NFTs are minted under phantomID — permanently unredeemable
```

The resulting `phantomID` is distinct from any legitimate single-hop trace of the original class, violating the one-canonical-ID-per-hop invariant and permanently locking the minted NFTs.

### Citations

**File:** x/nft-transfer/keeper/relay.go (L104-110)
```go
	// validate packet data upon receiving
	if err := data.ValidateBasic(); err != nil {
		return err
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	return k.processReceivedPacket(ctx, packet, data)
```

**File:** x/nft-transfer/types/packet.go (L41-44)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}
```

**File:** x/nft-transfer/keeper/packet.go (L148-148)
```go
	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(), packet.GetSourceChannel(), data.ClassId)
```

**File:** x/nft-transfer/keeper/packet.go (L154-165)
```go
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
```

**File:** x/nft-transfer/keeper/packet.go (L167-185)
```go
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

**File:** x/nft-transfer/types/trace.go (L49-52)
```go
func IsAwayFromOrigin(sourcePort, sourceChannel, fullClassPath string) bool {
	prefixClassID := GetClassPrefix(sourcePort, sourceChannel)
	return !strings.HasPrefix(fullClassPath, prefixClassID)
}
```

**File:** x/nft-transfer/types/trace.go (L61-75)
```go
func ParseClassTrace(rawClassID string) ClassTrace {
	classSplit := strings.Split(rawClassID, "/")

	if classSplit[0] == rawClassID {
		return ClassTrace{
			Path:        "",
			BaseClassId: rawClassID,
		}
	}

	return ClassTrace{
		Path:        strings.Join(classSplit[:len(classSplit)-1], "/"),
		BaseClassId: classSplit[len(classSplit)-1],
	}
}
```

**File:** x/nft-transfer/types/trace.go (L102-107)
```go
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
}
```

**File:** x/nft-transfer/types/trace.go (L125-128)
```go
func validateTraceIdentifiers(identifiers []string) error {
	if len(identifiers) == 0 || len(identifiers)%2 != 0 {
		return fmt.Errorf("trace info must come in pairs of port and channel identifiers '{portID}/{channelID}', got the identifiers: %s", identifiers)
	}
```
