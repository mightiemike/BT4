# Q3274: OnTimeoutPacket refundPacketToken source sink direction against sender ownership

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then timeout after class trace store changes or denom is recreated, causing `sender ownership` to diverge so the invariant `timeout callback cannot be replayed to refund the same packet twice` fails and the attacker can refund to wrong sender due to packet data/address trust mismatch, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: refund to wrong sender due to packet data/address trust mismatch by testing the source sink direction angle against `sender ownership` during `timeout after class trace store changes or denom is recreated`, with specific focus on escrow custody.
- Invariant to test: timeout callback cannot be replayed to refund the same packet twice; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
