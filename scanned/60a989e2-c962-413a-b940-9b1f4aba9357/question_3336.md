# Q3336: OnTimeoutPacket refundPacketToken source sink direction against escrowed NFTs

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send native NFT, allow timeout, then attempt resend, causing `escrowed NFTs` to diverge so the invariant `multi-token timeout refund is atomic or leaves no duplicate ownership` fails and the attacker can change class trace interpretation between send and timeout to unescrow wrong assets, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: change class trace interpretation between send and timeout to unescrow wrong assets by testing the source sink direction angle against `escrowed NFTs` during `send native NFT, allow timeout, then attempt resend`, with specific focus on packet token order.
- Invariant to test: multi-token timeout refund is atomic or leaves no duplicate ownership; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
