# Q3103: OnRecvPacket processReceivedPacket source sink direction against denom existence

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive after local denom already exists with conflicting owner or metadata, causing `denom existence` to diverge so the invariant `existing denoms cannot be hijacked by incoming voucher metadata` fails and the attacker can make packet source/dest channel confusion release assets from the wrong escrow address, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: make packet source/dest channel confusion release assets from the wrong escrow address by testing the source sink direction angle against `denom existence` during `receive after local denom already exists with conflicting owner or metadata`, with specific focus on acknowledgement refund finality.
- Invariant to test: existing denoms cannot be hijacked by incoming voucher metadata; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
