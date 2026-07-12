# Q3036: OnRecvPacket processReceivedPacket source sink direction against escrow address

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on class trace hash identity, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive a packet moving back to origin and unescrow from destination escrow address, causing `escrow address` to diverge so the invariant `valid packet mints or unescrows exactly one NFT per token id` fails and the attacker can make packet source/dest channel confusion release assets from the wrong escrow address, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: make packet source/dest channel confusion release assets from the wrong escrow address by testing the source sink direction angle against `escrow address` during `receive a packet moving back to origin and unescrow from destination escrow address`, with specific focus on class trace hash identity.
- Invariant to test: valid packet mints or unescrows exactly one NFT per token id; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
