# Q2928: createOutgoingPacket source sink direction against voucher burn

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet with repeated token ids and inspect escrow/burn loop behavior, causing `voucher burn` to diverge so the invariant `fullClassPath resolution matches stored class trace for IBC voucher ids` fails and the attacker can misorder token ids and uris to cause valuable metadata or token identity loss, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: misorder token ids and uris to cause valuable metadata or token identity loss by testing the source sink direction angle against `voucher burn` during `create packet with repeated token ids and inspect escrow/burn loop behavior`, with specific focus on packet token order.
- Invariant to test: fullClassPath resolution matches stored class trace for IBC voucher ids; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
