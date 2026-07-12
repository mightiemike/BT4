# Q2854: createOutgoingPacket source sink direction against tokenURIs

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet after transferring ownership just before send, causing `tokenURIs` to diverge so the invariant `fullClassPath resolution matches stored class trace for IBC voucher ids` fails and the attacker can send packet data proving ownership of tokens no longer owned by sender, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: send packet data proving ownership of tokens no longer owned by sender by testing the source sink direction angle against `tokenURIs` during `create packet after transferring ownership just before send`, with specific focus on voucher backing.
- Invariant to test: fullClassPath resolution matches stored class trace for IBC voucher ids; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
