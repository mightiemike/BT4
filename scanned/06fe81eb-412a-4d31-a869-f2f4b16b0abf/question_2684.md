# Q2684: SendTransfer source sink direction against packet data

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send a native NFT away from origin and then process timeout, causing `packet data` to diverge so the invariant `sender owns every token id before escrow or burn` fails and the attacker can create packet data with mismatched URI/id ordering that corrupts valuable NFT metadata, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: create packet data with mismatched URI/id ordering that corrupts valuable NFT metadata by testing the source sink direction angle against `packet data` during `send a native NFT away from origin and then process timeout`, with specific focus on escrow custody.
- Invariant to test: sender owns every token id before escrow or burn; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
