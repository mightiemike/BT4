### Title
IBC NFT Transfer Permanently Discards Per-Token `tokenData` on Every Cross-Chain Transfer - (`x/nft-transfer/keeper/packet.go`)

---

### Summary

The IBC NFT transfer module (`x/nft-transfer`) never includes per-NFT `tokenData` in the outgoing IBC packet, and always mints received NFTs with an empty `tokenData` field. Any NFT carrying on-chain `Data` that is transferred cross-chain permanently loses that data on the destination chain. On a timeout/refund path for sink-chain vouchers, the re-minted NFT also has empty `tokenData`, making the loss irreversible even when the transfer fails.

---

### Finding Description

`BaseNFT` carries four per-token fields: `Id`, `Name`, `URI`, and `Data`. [1](#0-0) 

The IBC packet type `NonFungibleTokenPacketData` only carries `TokenIds` and `TokenUris` — there is no `TokenData` field at all. [2](#0-1) 

In `createOutgoingPacket`, the loop over each token collects only `nft.GetURI()` into `tokenURIs`; `nft.GetData()` is never read and never placed in the packet. [3](#0-2) 

On the receiving side, `processReceivedPacket` calls `MintNFT` with a hard-coded empty string `""` as the `tokenData` argument (6th positional parameter): [4](#0-3) 

On the timeout/refund path, `refundPacketToken` does the same — re-minting sink-chain vouchers with empty `tokenData`: [5](#0-4) 

The `NewNonFungibleTokenPacketData` constructor confirms the packet schema has no slot for token data: [6](#0-5) 

---

### Impact Explanation

Every NFT that carries a non-empty `Data` field and is transferred via IBC arrives on the destination chain with `Data = ""`. This is permanent and irreversible: the destination chain has no way to recover the original value because it was never transmitted. For sink-chain voucher NFTs that time out, the refund re-mints the token with empty `Data`, so even the rollback path destroys the field. Any application logic, marketplace, or off-chain indexer that relies on on-chain `tokenData` to determine NFT attributes, provenance, or rights (analogous to royalty attribution in the external report) will observe a uniformly blank value — identical to the "single bucket" failure described in the reference finding.

---

### Likelihood Explanation

The trigger is a standard, unprivileged `MsgTransfer` transaction submitted by any NFT owner. No special role, governance action, or key compromise is required. Any user who mints an NFT with non-empty `Data` and then initiates an IBC transfer will silently lose that data. The code path is exercised on every cross-chain NFT transfer. [7](#0-6) 

---

### Recommendation

1. Add a `TokenData []string` field to `NonFungibleTokenPacketData` (proto and Go types).
2. In `createOutgoingPacket`, collect `nft.GetData()` alongside `nft.GetURI()` and populate the new field.
3. In `processReceivedPacket` and `refundPacketToken`, pass `data.TokenData[i]` instead of `""` to `MintNFT`.
4. Update `ValidateBasic` to enforce `len(TokenIds) == len(TokenData)` (allowing empty strings for tokens with no data).

---

### Proof of Concept

1. On chain A, mint an NFT: `MintNFT(denomID, tokenID, name, uri, "important-on-chain-data", owner)`.
2. Query the NFT: `GetNFT(denomID, tokenID)` → `Data == "important-on-chain-data"`.
3. Submit `MsgTransfer` from chain A to chain B for that token.
4. After the IBC packet is relayed and acknowledged, query the NFT on chain B: `GetNFT(ibcClassID, tokenID)` → `Data == ""`.
5. Transfer the NFT back to chain A (return path). The original escrowed NFT on chain A is unescrow'd correctly, but any further forward transfer from chain B will again produce `Data == ""` on the next hop.
6. Alternatively, let the packet time out: `refundPacketToken` re-mints the voucher on chain A with `Data == ""` — the original data is gone even on the source chain. [8](#0-7) [9](#0-8) [5](#0-4)

### Citations

**File:** x/nft/types/nft.pb.go (L27-33)
```go
type BaseNFT struct {
	Id    string `protobuf:"bytes,1,opt,name=id,proto3" json:"id,omitempty"`
	Name  string `protobuf:"bytes,2,opt,name=name,proto3" json:"name,omitempty"`
	URI   string `protobuf:"bytes,3,opt,name=uri,proto3" json:"uri,omitempty"`
	Data  string `protobuf:"bytes,4,opt,name=data,proto3" json:"data,omitempty"`
	Owner string `protobuf:"bytes,5,opt,name=owner,proto3" json:"owner,omitempty"`
}
```

**File:** x/nft-transfer/types/packet.pb.go (L28-41)
```go
type NonFungibleTokenPacketData struct {
	// the class_id of tokens to be transferred
	ClassId string `protobuf:"bytes,1,opt,name=class_id,json=classId,proto3" json:"class_id,omitempty"`
	// the class_uri of tokens to be transferred
	ClassUri string `protobuf:"bytes,2,opt,name=class_uri,json=classUri,proto3" json:"class_uri,omitempty"`
	// the non fungible tokens to be transferred (count should be equal to token_uris)
	TokenIds []string `protobuf:"bytes,3,rep,name=token_ids,json=tokenIds,proto3" json:"token_ids,omitempty"`
	// the non fungible tokens's uri to be transferred (count should be equal to token ids)
	TokenUris []string `protobuf:"bytes,4,rep,name=token_uris,json=tokenUris,proto3" json:"token_uris,omitempty"`
	// the sender address
	Sender string `protobuf:"bytes,5,opt,name=sender,proto3" json:"sender,omitempty"`
	// the recipient address on the destination chain
	Receiver string `protobuf:"bytes,6,opt,name=receiver,proto3" json:"receiver,omitempty"`
}
```

**File:** x/nft-transfer/keeper/packet.go (L43-48)
```go
		// we are sink chain, mint voucher back to sender
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, sender); err != nil {
				return err
			}
		}
```

**File:** x/nft-transfer/keeper/packet.go (L94-122)
```go
	for _, tokenID := range tokenIDs {
		nft, err := k.nftKeeper.GetNFT(ctx, classID, tokenID)
		if err != nil {
			return channeltypes.Packet{}, err
		}
		tokenURIs = append(tokenURIs, nft.GetURI())

		owner := nft.GetOwner()
		if !sender.Equals(owner) {
			return channeltypes.Packet{}, newsdkerrors.Wrap(sdkerrors.ErrUnauthorized, "not token owner")
		}

		if isAwayFromOrigin {
			// create the escrow address for the tokens
			escrowAddress := types.GetEscrowAddress(sourcePort, sourceChannel)
			if err := k.nftKeeper.TransferOwner(ctx, classID, tokenID, sender, escrowAddress); err != nil {
				return channeltypes.Packet{}, err
			}
		} else {
			// we are sink chain, burn the voucher
			if err := k.nftKeeper.BurnNFTUnverified(ctx, classID, tokenID, sender); err != nil {
				return channeltypes.Packet{}, err
			}
		}
	}

	packetData := types.NewNonFungibleTokenPacketData(
		fullClassPath, denom.Uri, tokenIDs, tokenURIs, sender.String(), receiver,
	)
```

**File:** x/nft-transfer/keeper/packet.go (L181-185)
```go
		for i, tokenID := range data.TokenIds {
			if err := k.nftKeeper.MintNFT(ctx, voucherClassID, tokenID, "", data.TokenUris[i], "", escrowAddress, receiver); err != nil {
				return err
			}
		}
```

**File:** x/nft-transfer/types/packet.go (L20-36)
```go
func NewNonFungibleTokenPacketData(
	classID string,
	classURI string,
	tokenIDs []string,
	tokenURI []string,
	sender string,
	receiver string,
) NonFungibleTokenPacketData {
	return NonFungibleTokenPacketData{
		ClassId:   classID,
		ClassUri:  classURI,
		TokenIds:  tokenIDs,
		TokenUris: tokenURI,
		Sender:    sender,
		Receiver:  receiver,
	}
}
```

**File:** x/nft-transfer/keeper/msg_server.go (L15-27)
```go
func (k Keeper) Transfer(goCtx context.Context, msg *types.MsgTransfer) (*types.MsgTransferResponse, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	sender, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return nil, err
	}
	if err := k.SendTransfer(
		ctx, msg.SourcePort, msg.SourceChannel, msg.ClassId, msg.TokenIds,
		sender, msg.Receiver, msg.TimeoutHeight, msg.TimeoutTimestamp,
	); err != nil {
		return nil, err
	}
```
