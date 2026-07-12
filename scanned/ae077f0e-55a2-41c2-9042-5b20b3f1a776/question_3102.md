# Q3102: OnRecvPacket processReceivedPacket source sink direction against denom existence

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive packet with token ids repeated or with boundary class id paths, causing `denom existence` to diverge so the invariant `escrow address selected for unescrow matches the original outgoing custody address` fails and the attacker can overwrite or reuse an existing denom to seize mint authority or metadata, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: overwrite or reuse an existing denom to seize mint authority or metadata by testing the source sink direction angle against `denom existence` during `receive packet with token ids repeated or with boundary class id paths`, with specific focus on acknowledgement refund finality.
- Invariant to test: escrow address selected for unescrow matches the original outgoing custody address; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
