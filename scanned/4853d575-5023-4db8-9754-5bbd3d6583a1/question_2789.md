# Q2789: SendTransfer source sink direction against NFT owner

## Question
Can NFT owner enter through `Keeper.SendTransfer` by send class ids through source, sink, and return-path channel combinations while controlling SourcePort, SourceChannel, ClassId, TokenIds, focusing on acknowledgement refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then send a native NFT away from origin and then process timeout, causing `NFT owner` to diverge so the invariant `sender owns every token id before escrow or burn` fails and the attacker can escrow or burn fewer NFTs than the packet claims and mint unbacked vouchers remotely, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.SendTransfer
- Entrypoint: Cosmos SDK MsgTransfer for nft-transfer
- Attacker controls: SourcePort, SourceChannel, ClassId, TokenIds, Sender, Receiver, timeout height, timeout timestamp
- Exploit idea: escrow or burn fewer NFTs than the packet claims and mint unbacked vouchers remotely by testing the source sink direction angle against `NFT owner` during `send a native NFT away from origin and then process timeout`, with specific focus on acknowledgement refund finality.
- Invariant to test: sender owns every token id before escrow or burn; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC app test over SendTransfer, createOutgoingPacket, OnAcknowledgementPacket, and OnTimeoutPacket; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
