# Q3392: OnTimeoutPacket refundPacketToken source sink direction against burned voucher NFTs

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on timeout refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send voucher toward origin, timeout, and mint voucher back, causing `burned voucher NFTs` to diverge so the invariant `timeout callback cannot be replayed to refund the same packet twice` fails and the attacker can mint voucher refund for a token that was escrowed rather than burned, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: mint voucher refund for a token that was escrowed rather than burned by testing the source sink direction angle against `burned voucher NFTs` during `send voucher toward origin, timeout, and mint voucher back`, with specific focus on timeout refund finality.
- Invariant to test: timeout callback cannot be replayed to refund the same packet twice; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
