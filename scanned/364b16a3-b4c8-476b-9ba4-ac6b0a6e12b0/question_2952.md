# Q2952: createOutgoingPacket source sink direction against NFT owner

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet from an ibc/{hash} class id whose trace resolves to a multi-hop path, causing `NFT owner` to diverge so the invariant `all token ownership changes are atomic with packet creation success` fails and the attacker can duplicate a token id in packet data and mint two remote vouchers for one NFT, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: duplicate a token id in packet data and mint two remote vouchers for one NFT by testing the source sink direction angle against `NFT owner` during `create packet from an ibc/{hash} class id whose trace resolves to a multi-hop path`, with specific focus on acknowledgement refund finality.
- Invariant to test: all token ownership changes are atomic with packet creation success; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
