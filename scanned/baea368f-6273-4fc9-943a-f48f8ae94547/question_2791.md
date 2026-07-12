# Q2791: SendTransfer source sink direction against NFT owner

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send multiple token ids with duplicate or reordered ids in one packet, causing `NFT owner` to diverge so the invariant `packet data token ids and token uris align one-to-one` fails and the attacker can misclassify source/sink direction and burn native NFTs or escrow vouchers incorrectly, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: misclassify source/sink direction and burn native NFTs or escrow vouchers incorrectly by testing the source sink direction angle against `NFT owner` during `send multiple token ids with duplicate or reordered ids in one packet`, with specific focus on acknowledgement refund finality.
- Invariant to test: packet data token ids and token uris align one-to-one; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
