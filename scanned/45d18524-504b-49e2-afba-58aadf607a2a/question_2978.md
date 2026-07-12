# Q2978: OnRecvPacket processReceivedPacket source sink direction against escrow address

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive after local denom already exists with conflicting owner or metadata, causing `escrow address` to diverge so the invariant `class trace hash uniquely binds path and base class id` fails and the attacker can use duplicate token ids to mint or transfer the same NFT twice, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: use duplicate token ids to mint or transfer the same NFT twice by testing the source sink direction angle against `escrow address` during `receive after local denom already exists with conflicting owner or metadata`, with specific focus on escrow custody.
- Invariant to test: class trace hash uniquely binds path and base class id; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
