# Q2888: createOutgoingPacket source sink direction against escrow address

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on class trace hash identity, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet with repeated token ids and inspect escrow/burn loop behavior, causing `escrow address` to diverge so the invariant `all token ownership changes are atomic with packet creation success` fails and the attacker can send packet data proving ownership of tokens no longer owned by sender, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: send packet data proving ownership of tokens no longer owned by sender by testing the source sink direction angle against `escrow address` during `create packet with repeated token ids and inspect escrow/burn loop behavior`, with specific focus on class trace hash identity.
- Invariant to test: all token ownership changes are atomic with packet creation success; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
