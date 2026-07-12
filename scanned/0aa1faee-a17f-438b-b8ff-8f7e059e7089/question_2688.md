# Q2688: SendTransfer source sink direction against packet data

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send at timeout boundary and then replay ack/timeout callbacks in valid order tests, causing `packet data` to diverge so the invariant `class trace direction correctly distinguishes source from sink chain` fails and the attacker can include duplicate token ids and later receive duplicate refunds or remote mints, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: include duplicate token ids and later receive duplicate refunds or remote mints by testing the source sink direction angle against `packet data` during `send at timeout boundary and then replay ack/timeout callbacks in valid order tests`, with specific focus on escrow custody.
- Invariant to test: class trace direction correctly distinguishes source from sink chain; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
