# Q2715: SendTransfer source sink direction against packet data

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send an IBC voucher back toward origin and then process acknowledgement error, causing `packet data` to diverge so the invariant `packet data token ids and token uris align one-to-one` fails and the attacker can create packet data with mismatched URI/id ordering that corrupts valuable NFT metadata, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: create packet data with mismatched URI/id ordering that corrupts valuable NFT metadata by testing the source sink direction angle against `packet data` during `send an IBC voucher back toward origin and then process acknowledgement error`, with specific focus on voucher backing.
- Invariant to test: packet data token ids and token uris align one-to-one; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
