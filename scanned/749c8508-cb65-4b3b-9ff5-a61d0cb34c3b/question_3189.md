# Q3189: OnAcknowledgementPacket refundPacketToken source sink direction against escrow address

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send voucher back toward origin, receive error ack, and mint voucher back locally, causing `escrow address` to diverge so the invariant `ack and timeout cannot both refund the same packet` fails and the attacker can receive both ack-error and timeout refunds and duplicate an NFT, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: receive both ack-error and timeout refunds and duplicate an NFT by testing the source sink direction angle against `escrow address` during `send voucher back toward origin, receive error ack, and mint voucher back locally`, with specific focus on packet token order.
- Invariant to test: ack and timeout cannot both refund the same packet; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
