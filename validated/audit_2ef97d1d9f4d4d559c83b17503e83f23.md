### Title
Unprivileged `MsgIssueDenom` Name Pre-Registration Permanently Blocks IBC NFT Voucher Denom Creation - (File: `x/nft-transfer/keeper/packet.go`)

---

### Summary

Any unprivileged user can permanently block IBC NFT transfers for a specific class/channel pair by pre-registering a native denom whose **Name** equals the deterministically predictable IBC voucher class ID (`ibc/<sha256_hex>`). When the IBC packet arrives, `processReceivedPacket` guards only against denom-ID collision but not denom-Name collision; `SetDenom` enforces uniqueness on both, so the voucher denom creation fails and the packet receives an error acknowledgement.

---

### Finding Description

`processReceivedPacket` in `x/nft-transfer/keeper/packet.go` creates the IBC voucher denom on first receipt of a new class:

```go
// packet.go lines 167-171
if !k.nftKeeper.HasDenomID(ctx, voucherClassID) {
    if err := k.nftKeeper.IssueDenom(ctx, voucherClassID, voucherClassID, "", data.ClassUri, escrowAddress); err != nil {
        return err
    }
}
``` [1](#0-0) 

`voucherClassID` is `ibc/<sha256(destPort/destChannel/classID)>` — fully deterministic and publicly computable from on-chain channel state.

`IssueDenom` delegates to `SetDenom`, which enforces **two independent uniqueness invariants**:

```go
// denom.go lines 27-33
if k.HasDenomID(ctx, denom.Id) {
    return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
}
if k.HasDenomNm(ctx, denom.Name) {
    return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
}
``` [2](#0-1) 

Because `IssueDenom` is called with `voucherClassID` as **both** the `id` and the `name` argument, the Name uniqueness check is a second, independent failure path that `processReceivedPacket` does not guard against.

`MsgIssueDenom` is open to any account with no permission check:

```go
// msg_server.go lines 25-34
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
    sender, err := sdk.AccAddressFromBech32(msg.Sender)
    ...
    if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
``` [3](#0-2) 

`ValidateDenomName` imposes no format restriction — any non-empty string is accepted:

```go
// validation.go lines 57-63
func ValidateDenomName(denomName string) error {
    denomName = strings.TrimSpace(denomName)
    if len(denomName) == 0 {
        return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
    }
    return nil
}
``` [4](#0-3) 

Therefore an attacker can submit `MsgIssueDenom{Id: "attackdenom", Name: "ibc/<predicted_hash>"}` with any valid lowercase-alphanumeric ID and the IBC voucher string as the Name. The ID check at line 167 passes (the IBC ID does not yet exist), but `SetDenom`'s Name check fails, returning an error that propagates out of `processReceivedPacket`.

---

### Impact Explanation

The corrupted invariant is: *a well-formed IBC-721 packet for a previously-unseen class must always succeed in creating the voucher denom on the destination chain.*

When `processReceivedPacket` returns an error, `OnRecvPacket` writes an error acknowledgement. The source chain's `OnAcknowledgementPacket` then calls `refundPacketToken`, returning the NFT to the sender. The NFT is not permanently lost, but the specific `(destPort, destChannel, classID)` triple is **permanently unserviceable** on this chain: the attacker's denom occupies the Name slot forever (denoms cannot be deleted), so every future packet for that class will hit the same error. The entire IBC NFT class is effectively blacklisted from this destination chain. [5](#0-4) 

---

### Likelihood Explanation

The `voucherClassID` is a pure SHA-256 hash of `destPort/destChannel/classID`, all of which are public on-chain data. An attacker monitoring mempool or chain state for an announced IBC NFT integration can compute the target hash offline before any transfer occurs and submit `MsgIssueDenom` with negligible cost (standard transaction fee). No special privilege, leaked key, or social engineering is required. The attack is permanent because `SetDenom` never allows a Name to be freed. [6](#0-5) 

---

### Recommendation

In `processReceivedPacket`, replace the bare `HasDenomID` guard with a combined check that also verifies the Name slot is free before calling `IssueDenom`. Alternatively, pass an empty string (or a distinct, non-colliding value) as the denom Name when creating IBC voucher denoms, so that the Name uniqueness check cannot be triggered by a user-supplied denom name. The root cause is that `SetDenom` enforces Name uniqueness globally across both user-created and protocol-created denoms without any namespace separation. [1](#0-0) [7](#0-6) 

---

### Proof of Concept

1. An IBC channel `nft-transfer/channel-0` exists between Chain A and Chain B (destination).
2. Chain A has an NFT class `myclass`. The attacker computes:
   ```
   prefixedClassID = "nft-transfer/channel-0/myclass"
   hash = sha256("nft-transfer/channel-0/myclass")  // hex string H
   voucherClassID = "ibc/" + H
   ```
3. On Chain B, the attacker submits:
   ```
   MsgIssueDenom { Id: "attackdenom", Name: "ibc/<H>", Schema: "", Sender: attacker }
   ```
   `ValidateDenomID("attackdenom")` passes; `ValidateDenomName("ibc/<H>")` passes (non-empty). `SetDenom` stores `KeyDenomName("ibc/<H>") → "attackdenom"`.
4. A legitimate user on Chain A sends an NFT of class `myclass` to Chain B via IBC.
5. Chain B's `OnRecvPacket` → `processReceivedPacket`:
   - `HasDenomID(ctx, "ibc/<H>")` → **false** (guard passes)
   - `IssueDenom(ctx, "ibc/<H>", "ibc/<H>", ...)` → `SetDenom` → `HasDenomNm(ctx, "ibc/<H>")` → **true** → returns `ErrInvalidDenom`
6. `processReceivedPacket` returns error; `OnRecvPacket` writes error ack; source chain refunds sender.
7. Every subsequent transfer of `myclass` to Chain B via `channel-0` fails identically. The class is permanently blocked. [8](#0-7) [9](#0-8)

### Citations

**File:** x/nft-transfer/keeper/packet.go (L153-171)
```go
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
```

**File:** x/nft/keeper/denom.go (L26-39)
```go
func (k Keeper) SetDenom(ctx sdk.Context, denom types.Denom) error {
	if k.HasDenomID(ctx, denom.Id) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomID %s has already exists", denom.Id)
	}

	if k.HasDenomNm(ctx, denom.Name) {
		return sdkerrors.Wrapf(types.ErrInvalidDenom, "denomName %s has already exists", denom.Name)
	}

	store := ctx.KVStore(k.storeKey)
	bz := k.cdc.MustMarshal(&denom)
	store.Set(types.KeyDenomID(denom.Id), bz)
	store.Set(types.KeyDenomName(denom.Name), []byte(denom.Id))
	return nil
```

**File:** x/nft/keeper/msg_server.go (L25-34)
```go
func (m msgServer) IssueDenom(goCtx context.Context, msg *types.MsgIssueDenom) (*types.MsgIssueDenomResponse, error) {
	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}

	ctx := sdk.UnwrapSDKContext(goCtx)
	if err := m.Keeper.IssueDenom(ctx, msg.Id, msg.Name, msg.Schema, msg.Uri, sender); err != nil {
		return nil, err
	}
```

**File:** x/nft/types/validation.go (L57-63)
```go
func ValidateDenomName(denomName string) error {
	denomName = strings.TrimSpace(denomName)
	if len(denomName) == 0 {
		return sdkerrors.Wrapf(ErrInvalidDenomName, "denom name(%s) can not be space", denomName)
	}
	return nil
}
```

**File:** x/nft-transfer/types/trace.go (L95-107)
```go
func (ct ClassTrace) Hash() tmbytes.HexBytes {
	hash := sha256.Sum256([]byte(ct.GetFullClassPath()))
	return hash[:]
}

// IBCClassID a classID for an ICS721 non-fungible token in the format
// 'ibc/{hash(tracePath + BaseClassId)}'. If the trace is empty, it will return the base classID.
func (ct ClassTrace) IBCClassID() string {
	if ct.Path != "" {
		return fmt.Sprintf("%s/%s", ClassPrefix, ct.Hash())
	}
	return ct.BaseClassId
}
```
