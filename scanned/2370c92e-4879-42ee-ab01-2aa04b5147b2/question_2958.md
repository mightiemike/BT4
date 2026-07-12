# Q2958: createOutgoingPacket source sink direction against voucher burn

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet with repeated token ids and inspect escrow/burn loop behavior, causing `voucher burn` to diverge so the invariant `escrow address is derived only from the sending port and channel` fails and the attacker can misresolve an IBC class hash and unback a voucher from the wrong trace, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: misresolve an IBC class hash and unback a voucher from the wrong trace by testing the source sink direction angle against `voucher burn` during `create packet with repeated token ids and inspect escrow/burn loop behavior`, with specific focus on acknowledgement refund finality.
- Invariant to test: escrow address is derived only from the sending port and channel; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
