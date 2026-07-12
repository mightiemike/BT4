# Q2683: SendTransfer source sink direction against voucher burn state

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send at timeout boundary and then replay ack/timeout callbacks in valid order tests, causing `voucher burn state` to diverge so the invariant `native NFTs are escrowed exactly once and vouchers are burned exactly once` fails and the attacker can include duplicate token ids and later receive duplicate refunds or remote mints, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: include duplicate token ids and later receive duplicate refunds or remote mints by testing the source sink direction angle against `voucher burn state` during `send at timeout boundary and then replay ack/timeout callbacks in valid order tests`, with specific focus on escrow custody.
- Invariant to test: native NFTs are escrowed exactly once and vouchers are burned exactly once; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
