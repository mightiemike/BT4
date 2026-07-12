# Q2935: createOutgoingPacket source sink direction against packet bytes

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet from a class id with path prefix equal to source channel, causing `packet bytes` to diverge so the invariant `source/sink decision uses the raw full class path, not an ambiguous hash` fails and the attacker can send packet data proving ownership of tokens no longer owned by sender, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: send packet data proving ownership of tokens no longer owned by sender by testing the source sink direction angle against `packet bytes` during `create packet from a class id with path prefix equal to source channel`, with specific focus on packet token order.
- Invariant to test: source/sink decision uses the raw full class path, not an ambiguous hash; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
