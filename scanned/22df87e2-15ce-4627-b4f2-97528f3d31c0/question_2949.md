# Q2949: createOutgoingPacket source sink direction against escrow address

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet after transferring ownership just before send, causing `escrow address` to diverge so the invariant `tokenURI index order matches tokenIDs and cannot be shifted by duplicates or failures` fails and the attacker can misresolve an IBC class hash and unback a voucher from the wrong trace, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: misresolve an IBC class hash and unback a voucher from the wrong trace by testing the source sink direction angle against `escrow address` during `create packet after transferring ownership just before send`, with specific focus on acknowledgement refund finality.
- Invariant to test: tokenURI index order matches tokenIDs and cannot be shifted by duplicates or failures; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
