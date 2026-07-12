# Q3008: OnRecvPacket processReceivedPacket source sink direction against escrow address

## Question
Can IBC packet sender through a valid channel enter through `Keeper.OnRecvPacket / Keeper.processReceivedPacket` by send class ids through source, sink, and return-path channel combinations while controlling packet source/dest port, packet source/dest channel, ClassId, TokenIds, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then receive after local denom already exists with conflicting owner or metadata, causing `escrow address` to diverge so the invariant `receiver must be a valid local address before ownership changes` fails and the attacker can overwrite or reuse an existing denom to seize mint authority or metadata, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnRecvPacket / Keeper.processReceivedPacket
- Entrypoint: IBC NFT transfer receive callback
- Attacker controls: packet source/dest port, packet source/dest channel, ClassId, TokenIds, TokenUris, Sender, Receiver
- Exploit idea: overwrite or reuse an existing denom to seize mint authority or metadata by testing the source sink direction angle against `escrow address` during `receive after local denom already exists with conflicting owner or metadata`, with specific focus on voucher backing.
- Invariant to test: receiver must be a valid local address before ownership changes; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC receive test over OnRecvPacket, processReceivedPacket, class trace store, and NFT ownership; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
