# Q3421: ParseClassTrace IsAwayFromOrigin RemoveClassPrefix IBCClassID source sink direction against voucher class id

## Question
Can NFT sender or IBC packet sender enter through `ParseClassTrace / IsAwayFromOrigin / RemoveClassPrefix / IBCClassID` by send class ids through source, sink, and return-path channel combinations while controlling class id path, port ids, channel ids, ibc hash, focusing on escrow custody, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then resolve ibc/{hash} to full class path before source/sink decision, causing `voucher class id` to diverge so the invariant `RemoveClassPrefix is called only when classID has the expected prefix` fails and the attacker can exploit a class trace prefix ambiguity or trace/hash lookup mismatch to mint an unbacked voucher, leading to High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact?

## Target
- File/function: x/nft-transfer/types/trace.go::ParseClassTrace / IsAwayFromOrigin / RemoveClassPrefix / IBCClassID
- Entrypoint: MsgTransfer and IBC receive/refund class-id handling
- Attacker controls: class id path, port ids, channel ids, ibc hash, base class id
- Exploit idea: exploit a class trace prefix ambiguity or trace/hash lookup mismatch to mint an unbacked voucher by testing the source sink direction angle against `voucher class id` during `resolve ibc/{hash} to full class path before source/sink decision`, with specific focus on escrow custody.
- Invariant to test: RemoveClassPrefix is called only when classID has the expected prefix; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: High cross-module NFT and nft-transfer ownership or escrow accounting corruption with asset-loss impact; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: unit and keeper tests over trace parsing plus full SendTransfer/OnRecvPacket round trips; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
