# Q3150: OnAcknowledgementPacket refundPacketToken source sink direction against voucher mint

## Question
Can IBC packet participant receiving an error acknowledgement enter through `Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken` by send class ids through source, sink, and return-path channel combinations while controlling ack response, packet data, source port/channel, ClassId, focusing on voucher backing, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then process ack error with duplicate token ids in packet data, causing `voucher mint` to diverge so the invariant `refund class id and escrow address match the original send direction` fails and the attacker can duplicate token ids in refund data to mint or transfer more NFTs than originally sent, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/keeper/relay.go::Keeper.OnAcknowledgementPacket / Keeper.refundPacketToken
- Entrypoint: IBC acknowledgement callback for nft-transfer
- Attacker controls: ack response, packet data, source port/channel, ClassId, TokenIds, TokenUris, Sender
- Exploit idea: duplicate token ids in refund data to mint or transfer more NFTs than originally sent by testing the source sink direction angle against `voucher mint` during `process ack error with duplicate token ids in packet data`, with specific focus on voucher backing.
- Invariant to test: refund class id and escrow address match the original send direction; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: IBC lifecycle test over failed acknowledgement and timeout with bank/NFT owner assertions; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
