# Q3398: OnTimeoutPacket refundPacketToken source sink direction against sender ownership

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on timeout refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then timeout multi-token packet where one token refund fails, causing `sender ownership` to diverge so the invariant `multi-token timeout refund is atomic or leaves no duplicate ownership` fails and the attacker can replay timeout and duplicate NFTs or vouchers, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: replay timeout and duplicate NFTs or vouchers by testing the source sink direction angle against `sender ownership` during `timeout multi-token packet where one token refund fails`, with specific focus on timeout refund finality.
- Invariant to test: multi-token timeout refund is atomic or leaves no duplicate ownership; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
