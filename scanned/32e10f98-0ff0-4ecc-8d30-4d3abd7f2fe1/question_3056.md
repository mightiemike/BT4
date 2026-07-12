# Q3056: OnRecvPacket processReceivedPacket source sink direction against class trace

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive a packet moving back to origin and unescrow from destination escrow address, causing `class trace` to diverge so the invariant `valid packet mints or unescrows exactly one NFT per token id` fails and the attacker can use duplicate token ids to mint or transfer the same NFT twice, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: use duplicate token ids to mint or transfer the same NFT twice by testing the source sink direction angle against `class trace` during `receive a packet moving back to origin and unescrow from destination escrow address`, with specific focus on packet token order.
- Invariant to test: valid packet mints or unescrows exactly one NFT per token id; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
