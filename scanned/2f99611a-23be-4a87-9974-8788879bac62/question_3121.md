# Q3121: OnAcknowledgementPacket refundPacketToken source sink direction against sender NFT ownership

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then process ack after local ownership or denom state changed unexpectedly, causing `sender NFT ownership` to diverge so the invariant `success acknowledgement never refunds assets` fails and the attacker can refund to attacker by manipulating packet Sender or class direction, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: refund to attacker by manipulating packet Sender or class direction by testing the source sink direction angle against `sender NFT ownership` during `process ack after local ownership or denom state changed unexpectedly`, with specific focus on escrow custody.
- Invariant to test: success acknowledgement never refunds assets; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
