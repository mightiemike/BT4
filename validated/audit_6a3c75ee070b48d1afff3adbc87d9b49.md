### Title
Missing Enforcement of Non-Zero Timeout in ICS-721 NFT Transfer Allows Permanently Frozen NFTs - (File: x/nft-transfer/types/msgs.go)

### Summary

`MsgTransfer.ValidateBasic()` in the `x/nft-transfer` module does not enforce that at least one of `TimeoutHeight` or `TimeoutTimestamp` is non-zero. The IBC packet timeout is the sole recovery mechanism for stuck NFT transfers: when a packet is never relayed or the destination chain fails to process it, `OnTimeoutPacket` fires and calls `refundPacketToken` to return the NFTs to the sender. If both timeouts are zero, no timeout ever fires, and NFTs escrowed on the source chain are permanently locked with no recovery path.

### Finding Description

`MsgTransfer` carries two optional timeout fields: `TimeoutHeight` (disabled when `{0,0}`) and `TimeoutTimestamp` (disabled when `0`). The proto definition explicitly documents both as individually disableable: [1](#0-0) 

`ValidateBasic()` validates port, channel, classId, tokenIds, sender, and receiver, but contains no check that at least one timeout is non-zero: [2](#0-1) 

The comment at line 51 explicitly acknowledges this: `"NOTE: timeout height or timestamp values can be 0 to disable the timeout."` — but no guard prevents both from being zero simultaneously.

The CLI enforces a non-zero timestamp only for the **relative** timeout path: [3](#0-2) 

When `--absolute-timeouts` is passed, this guard is skipped entirely. A user can pass `--absolute-timeouts --packet-timeout-height 0-0 --packet-timeout-timestamp 0` and the message is accepted. More critically, any direct gRPC or REST submission of `MsgTransfer` with both fields zeroed bypasses the CLI entirely and reaches `SendTransfer` without rejection: [4](#0-3) 

`SendTransfer` passes both timeout values directly to `createOutgoingPacket` and `ics4Wrapper.SendPacket` with no additional validation: [5](#0-4) 

When the packet is created with both timeouts at zero, `OnTimeoutPacket` — the only refund path — can never be triggered: [6](#0-5) 

The refund logic in `refundPacketToken` correctly unescrows or re-mints NFTs back to the sender, but it is unreachable without a timeout: [7](#0-6) 

### Impact Explanation

NFTs transferred with both timeouts at zero are escrowed (source-chain native) or burned (voucher return) at send time: [8](#0-7) 

If the destination chain never processes the packet — due to a halted chain, a non-existent receiver address causing an error ack, or a permanently closed channel — the escrowed NFTs are locked in the escrow address forever. There is no governance or admin path to recover them; the only recovery mechanism is `OnTimeoutPacket`, which requires a non-zero timeout to have been set at send time.

The corrupted invariant: NFT ownership. The sender loses custody of their NFT (it moves to the escrow address) and never regains it.

### Likelihood Explanation

The CLI default for `--packet-timeout-timestamp` is `DefaultRelativePacketTimeoutTimestamp` (10 minutes), so ordinary CLI users are protected by default. However:

1. Any user submitting `MsgTransfer` directly via gRPC or REST with `timeout_height: {revision_number: 0, revision_height: 0}` and `timeout_timestamp: 0` bypasses all CLI guards.
2. A CLI user passing `--absolute-timeouts --packet-timeout-timestamp 0` (overriding the default) also produces a zero-timeout packet, since the absolute-timeout branch has no zero-check. [3](#0-2) 

This is reachable by any unprivileged account holding an NFT. No special role or leaked key is required.

### Recommendation

Add a check in `MsgTransfer.ValidateBasic()` that at least one of `TimeoutHeight` and `TimeoutTimestamp` is non-zero:

```go
if msg.TimeoutHeight.IsZero() && msg.TimeoutTimestamp == 0 {
    return sdkerrors.Wrap(ErrInvalidPacket,
        "timeout height and timeout timestamp cannot both be 0")
}
``` [9](#0-8) 

Additionally, add the same guard in `SendTransfer` as a defense-in-depth check, and document the behavior explicitly so integrators using the gRPC API are aware that omitting both timeouts permanently freezes NFTs in transit.

### Proof of Concept

1. Alice owns NFT `(classId="cryptoCat", tokenId="kitty")` on chain A.
2. Alice submits via gRPC:
   ```json
   {
     "source_port": "nft-transfer",
     "source_channel": "channel-0",
     "class_id": "cryptoCat",
     "token_ids": ["kitty"],
     "sender": "cro1alice...",
     "receiver": "cro1bob...",
     "timeout_height": {"revision_number": "0", "revision_height": "0"},
     "timeout_timestamp": "0"
   }
   ```
3. `ValidateBasic()` passes — no timeout check exists.
4. `createOutgoingPacket` escrows `kitty` to the escrow address for `(nft-transfer, channel-0)`.
5. The packet is committed on-chain with no timeout.
6. If the destination chain is halted, the receiver address is invalid, or the channel is closed, `OnTimeoutPacket` never fires.
7. `kitty` remains permanently locked in the escrow address. Alice has no recovery path. [8](#0-7) [10](#0-9)

### Citations

**File:** proto/chainmain/nft_transfer/v1/tx.proto (L37-43)
```text
  // Timeout height relative to the current block height.
  // The timeout is disabled when set to 0.
  ibc.core.client.v1.Height timeout_height = 7
      [(gogoproto.moretags) = "yaml:\"timeout_height\"", (gogoproto.nullable) = false];
  // Timeout timestamp in absolute nanoseconds since unix epoch.
  // The timeout is disabled when set to 0.
  uint64 timeout_timestamp = 8 [(gogoproto.moretags) = "yaml:\"timeout_timestamp\""];
```

**File:** x/nft-transfer/types/msgs.go (L50-88)
```go
// ValidateBasic performs a basic check of the MsgTransfer fields.
// NOTE: timeout height or timestamp values can be 0 to disable the timeout.
// NOTE: The recipient addresses format is not validated as the format defined by
// the chain is not known to IBC.
func (msg MsgTransfer) ValidateBasic() error {
	if err := host.PortIdentifierValidator(msg.SourcePort); err != nil {
		return newsdkerrors.Wrap(err, "invalid source port ID")
	}
	if msg.SourcePort != PortID {
		return newsdkerrors.Wrapf(ErrInvalidSourcePort, "source port must be %q", PortID)
	}
	if err := host.ChannelIdentifierValidator(msg.SourceChannel); err != nil {
		return newsdkerrors.Wrap(err, "invalid source channel ID")
	}

	if strings.TrimSpace(msg.ClassId) == "" {
		return newsdkerrors.Wrap(ErrInvalidClassID, "classId cannot be blank")
	}

	if len(msg.TokenIds) == 0 {
		return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
	}

	for _, tokenID := range msg.TokenIds {
		if strings.TrimSpace(tokenID) == "" {
			return newsdkerrors.Wrap(ErrInvalidTokenID, "tokenId cannot be blank")
		}
	}

	// NOTE: sender format must be validated as it is required by the GetSigners function.
	_, err := sdk.AccAddressFromBech32(msg.Sender)
	if err != nil {
		return newsdkerrors.Wrapf(sdkerrors.ErrInvalidAddress, "string could not be parsed as address: %v", err)
	}
	if strings.TrimSpace(msg.Receiver) == "" {
		return newsdkerrors.Wrap(sdkerrors.ErrInvalidAddress, "missing recipient address")
	}
	return nil
}
```

**File:** x/nft-transfer/client/cli/tx.go (L79-95)
```go
			if !absoluteTimeouts {
				if !timeoutHeight.IsZero() {
					return errors.New("relative timeouts using block height is not supported")
				}

				if timeoutTimestamp == 0 {
					return errors.New("relative timeouts must provide a non zero value timestamp")
				}

				// use local clock time as reference time for calculating timeout timestamp.
				now := time.Now().UnixNano()
				if now <= 0 {
					return errors.New("local clock time is not greater than Jan 1st, 1970 12:00 AM")
				}

				timeoutTimestamp = uint64(now) + timeoutTimestamp
			}
```

**File:** x/nft-transfer/keeper/relay.go (L39-93)
```go
func (k Keeper) SendTransfer(
	ctx sdk.Context,
	sourcePort,
	sourceChannel,
	classID string,
	tokenIDs []string,
	sender sdk.AccAddress,
	receiver string,
	timeoutHeight clienttypes.Height,
	timeoutTimestamp uint64,
) error {
	if sourcePort != types.PortID {
		return sdkerrors.Wrapf(types.ErrInvalidSourcePort, "source port must be %q", types.PortID)
	}

	sourceChannelEnd, found := k.channelKeeper.GetChannel(ctx, sourcePort, sourceChannel)
	if !found {
		return sdkerrors.Wrapf(channeltypes.ErrChannelNotFound, "port ID (%s) channel ID (%s)", sourcePort, sourceChannel)
	}

	destinationPort := sourceChannelEnd.Counterparty.PortId
	destinationChannel := sourceChannelEnd.Counterparty.ChannelId

	// get the next sequence
	sequence, found := k.channelKeeper.GetNextSequenceSend(ctx, sourcePort, sourceChannel)
	if !found {
		return sdkerrors.Wrapf(
			channeltypes.ErrSequenceSendNotFound,
			"source port: %s, source channel: %s", sourcePort, sourceChannel,
		)
	}

	// See spec for this logic: https://github.com/cosmos/ibc/blob/master/spec/app/ics-721-nft-transfer/README.md#packet-relay
	packet, err := k.createOutgoingPacket(ctx,
		sourcePort,
		sourceChannel,
		destinationPort,
		destinationChannel,
		classID,
		tokenIDs,
		sender,
		receiver,
		sequence,
		timeoutHeight,
		timeoutTimestamp,
	)
	if err != nil {
		return err
	}

	if _, err := k.ics4Wrapper.SendPacket(ctx, sourcePort, sourceChannel, timeoutHeight, timeoutTimestamp, packet.GetData()); err != nil {
		return err
	}

	return nil
```

**File:** x/nft-transfer/keeper/relay.go (L128-132)
```go
// OnTimeoutPacket refunds the sender since the original packet sent was
// never received and has been timed out.
func (k Keeper) OnTimeoutPacket(ctx sdk.Context, channelVersion string, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	return k.refundPacketToken(ctx, packet, data)
}
```

**File:** x/nft-transfer/keeper/packet.go (L21-51)
```go
func (k Keeper) refundPacketToken(ctx sdk.Context, packet channeltypes.Packet, data types.NonFungibleTokenPacketData) error {
	sender, err := sdk.AccAddressFromBech32(data.Sender)
	if err != nil {
		return err
	}

	classTrace := types.ParseClassTrace(data.ClassId)
	voucherClassID := classTrace.IBCClassID()

	isAwayFromOrigin := types.IsAwayFromOrigin(packet.GetSourcePort(),
		packet.GetSourceChannel(), data.ClassId)

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
	}

	return nil
```

**File:** x/nft-transfer/keeper/packet.go (L106-117)
```go
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
```
