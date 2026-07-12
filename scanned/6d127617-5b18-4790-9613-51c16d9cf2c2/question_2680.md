# Q2680: SendTransfer source sink direction against voucher burn state

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send an IBC voucher back toward origin and then process acknowledgement error, causing `voucher burn state` to diverge so the invariant `next sequence and packet data cannot be reused to refund twice` fails and the attacker can misclassify source/sink direction and burn native NFTs or escrow vouchers incorrectly, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: misclassify source/sink direction and burn native NFTs or escrow vouchers incorrectly by testing the source sink direction angle against `voucher burn state` during `send an IBC voucher back toward origin and then process acknowledgement error`, with specific focus on escrow custody.
- Invariant to test: next sequence and packet data cannot be reused to refund twice; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
