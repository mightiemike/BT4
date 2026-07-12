# Q3375: OnTimeoutPacket refundPacketToken source sink direction against sender ownership

## Question
Can NFT sender whose packet times out enter through `Keeper.OnTimeoutPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling packet timeout, packet data, source channel, ClassId, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then timeout and then process an error acknowledgement in a replay harness, causing `sender ownership` to diverge so the invariant `multi-token timeout refund is atomic or leaves no duplicate ownership` fails and the attacker can change class trace interpretation between send and timeout to unescrow wrong assets, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnTimeoutPacket / Keeper.refundPacketToken
- Entrypoint: IBC timeout callback for nft-transfer
- Attacker controls: packet timeout, packet data, source channel, ClassId, TokenIds, Sender
- Exploit idea: change class trace interpretation between send and timeout to unescrow wrong assets by testing the source sink direction angle against `sender ownership` during `timeout and then process an error acknowledgement in a replay harness`, with specific focus on acknowledgement refund finality.
- Invariant to test: multi-token timeout refund is atomic or leaves no duplicate ownership; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC timeout test with repeated callbacks and exact NFT ownership assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
