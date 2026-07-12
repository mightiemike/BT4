# Q3120: OnAcknowledgementPacket refundPacketToken source sink direction against sender NFT ownership

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then process ack error with duplicate token ids in packet data, causing `sender NFT ownership` to diverge so the invariant `failed voucher transfer remints exactly the burned voucher token ids back to sender` fails and the attacker can unescrow assets from the wrong channel escrow address, leading to Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: unescrow assets from the wrong channel escrow address by testing the source sink direction angle against `sender NFT ownership` during `process ack error with duplicate token ids in packet data`, with specific focus on escrow custody.
- Invariant to test: failed voucher transfer remints exactly the burned voucher token ids back to sender; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical unbacked NFT voucher minting, duplicate withdrawal, or unauthorized unescrow; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
