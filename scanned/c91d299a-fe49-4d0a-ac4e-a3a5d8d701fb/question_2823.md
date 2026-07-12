# Q2823: createOutgoingPacket source sink direction against tokenURIs

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet with repeated token ids and inspect escrow/burn loop behavior, causing `tokenURIs` to diverge so the invariant `escrow address is derived only from the sending port and channel` fails and the attacker can partially escrow or burn tokens before an error and leave assets stuck or duplicated, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: partially escrow or burn tokens before an error and leave assets stuck or duplicated by testing the source sink direction angle against `tokenURIs` during `create packet with repeated token ids and inspect escrow/burn loop behavior`, with specific focus on escrow custody.
- Invariant to test: escrow address is derived only from the sending port and channel; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
