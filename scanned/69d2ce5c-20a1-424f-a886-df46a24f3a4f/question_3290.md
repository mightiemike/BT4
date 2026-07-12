# Q3290: OnTimeoutPacket refundPacketToken source sink direction against escrowed NFTs

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then timeout and then process an error acknowledgement in a replay harness, causing `escrowed NFTs` to diverge so the invariant `voucher refund cannot mint unbacked tokens beyond those burned` fails and the attacker can refund to wrong sender due to packet data/address trust mismatch, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: refund to wrong sender due to packet data/address trust mismatch by testing the source sink direction angle against `escrowed NFTs` during `timeout and then process an error acknowledgement in a replay harness`, with specific focus on voucher backing.
- Invariant to test: voucher refund cannot mint unbacked tokens beyond those burned; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
