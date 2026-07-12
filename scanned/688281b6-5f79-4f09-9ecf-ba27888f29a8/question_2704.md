# Q2704: SendTransfer source sink direction against escrow address

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send a native NFT away from origin and then process timeout, causing `escrow address` to diverge so the invariant `class trace direction correctly distinguishes source from sink chain` fails and the attacker can reuse packet lifecycle state to trigger both timeout refund and ack refund, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: reuse packet lifecycle state to trigger both timeout refund and ack refund by testing the source sink direction angle against `escrow address` during `send a native NFT away from origin and then process timeout`, with specific focus on voucher backing.
- Invariant to test: class trace direction correctly distinguishes source from sink chain; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
