# Q2731: SendTransfer source sink direction against NFT owner

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on class trace hash identity, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send multiple token ids with duplicate or reordered ids in one packet, causing `NFT owner` to diverge so the invariant `native NFTs are escrowed exactly once and vouchers are burned exactly once` fails and the attacker can include duplicate token ids and later receive duplicate refunds or remote mints, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: include duplicate token ids and later receive duplicate refunds or remote mints by testing the source sink direction angle against `NFT owner` during `send multiple token ids with duplicate or reordered ids in one packet`, with specific focus on class trace hash identity.
- Invariant to test: native NFTs are escrowed exactly once and vouchers are burned exactly once; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
