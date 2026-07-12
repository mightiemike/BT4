# Q3138: OnAcknowledgementPacket refundPacketToken source sink direction against escrow address

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send native NFT, receive error acknowledgement, then attempt timeout-like refund path, causing `escrow address` to diverge so the invariant `refund class id and escrow address match the original send direction` fails and the attacker can receive both ack-error and timeout refunds and duplicate an NFT, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: receive both ack-error and timeout refunds and duplicate an NFT by testing the source sink direction angle against `escrow address` during `send native NFT, receive error acknowledgement, then attempt timeout-like refund path`, with specific focus on voucher backing.
- Invariant to test: refund class id and escrow address match the original send direction; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
