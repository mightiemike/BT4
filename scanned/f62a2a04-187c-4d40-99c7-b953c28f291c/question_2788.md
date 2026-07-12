# Q2788: SendTransfer source sink direction against class trace

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on packet token order, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send at timeout boundary and then replay ack/timeout callbacks in valid order tests, causing `class trace` to diverge so the invariant `next sequence and packet data cannot be reused to refund twice` fails and the attacker can escrow or burn fewer NFTs than the packet claims and mint unbacked vouchers remotely, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: escrow or burn fewer NFTs than the packet claims and mint unbacked vouchers remotely by testing the source sink direction angle against `class trace` during `send at timeout boundary and then replay ack/timeout callbacks in valid order tests`, with specific focus on packet token order.
- Invariant to test: next sequence and packet data cannot be reused to refund twice; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
