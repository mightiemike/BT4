# Q3220: OnAcknowledgementPacket refundPacketToken source sink direction against sender NFT ownership

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then process ack error with duplicate token ids in packet data, causing `sender NFT ownership` to diverge so the invariant `ack and timeout cannot both refund the same packet` fails and the attacker can receive both ack-error and timeout refunds and duplicate an NFT, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: receive both ack-error and timeout refunds and duplicate an NFT by testing the source sink direction angle against `sender NFT ownership` during `process ack error with duplicate token ids in packet data`, with specific focus on acknowledgement refund finality.
- Invariant to test: ack and timeout cannot both refund the same packet; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
