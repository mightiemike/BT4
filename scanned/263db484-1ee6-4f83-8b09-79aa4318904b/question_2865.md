# Q2865: createOutgoingPacket source sink direction against NFT owner

## Question
Can NFT owner enter through `Keeper.createOutgoingPacket` by send class ids through source, sink, and return-path channel combinations while controlling classID, tokenIDs, sender, receiver, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then create packet from a class id with path prefix equal to source channel, causing `NFT owner` to diverge so the invariant `tokenURI index order matches tokenIDs and cannot be shifted by duplicates or failures` fails and the attacker can duplicate a token id in packet data and mint two remote vouchers for one NFT, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/packet.go::Keeper.createOutgoingPacket
- Entrypoint: MsgTransfer through Keeper.SendTransfer
- Attacker controls: classID, tokenIDs, sender, receiver, source channel, destination channel, timeout
- Exploit idea: duplicate a token id in packet data and mint two remote vouchers for one NFT by testing the source sink direction angle against `NFT owner` during `create packet from a class id with path prefix equal to source channel`, with specific focus on voucher backing.
- Invariant to test: tokenURI index order matches tokenIDs and cannot be shifted by duplicates or failures; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test directly invoking SendTransfer with multi-token packets and class trace setup; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
