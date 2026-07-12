# Q2780: SendTransfer source sink direction against next sequence

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send an IBC voucher back toward origin and then process acknowledgement error, causing `next sequence` to diverge so the invariant `packet data token ids and token uris align one-to-one` fails and the attacker can include duplicate token ids and later receive duplicate refunds or remote mints, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: include duplicate token ids and later receive duplicate refunds or remote mints by testing the source sink direction angle against `next sequence` during `send an IBC voucher back toward origin and then process acknowledgement error`, with specific focus on packet token order.
- Invariant to test: packet data token ids and token uris align one-to-one; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
