# Q3555: ParseClassTrace IsAwayFromOrigin RemoveClassPrefix IBCClassID source sink direction against escrow address selection

## Question
Can NFT sender or IBC packet sender enter through `ParseClassTrace / IsAwayFromOrigin / RemoveClassPrefix / IBCClassID` by send class ids through source, sink, and return-path channel combinations while controlling class id path, port ids, channel ids, ibc hash, focusing on timeout refund finality, under the precondition that a valid ICS721 channel and native or voucher NFT exist, then return a voucher through the inverse channel path and remove prefix, causing `escrow address selection` to diverge so the invariant `base class id cannot be interpreted as path segments that alter escrow semantics` fails and the attacker can exploit a class trace prefix ambiguity or trace/hash lookup mismatch to mint an unbacked voucher, leading to Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle?

## Target
- File/function: x/nft-transfer/types/trace.go::ParseClassTrace / IsAwayFromOrigin / RemoveClassPrefix / IBCClassID
- Entrypoint: MsgTransfer and IBC receive/refund class-id handling
- Attacker controls: class id path, port ids, channel ids, ibc hash, base class id
- Exploit idea: exploit a class trace prefix ambiguity or trace/hash lookup mismatch to mint an unbacked voucher by testing the source sink direction angle against `escrow address selection` during `return a voucher through the inverse channel path and remove prefix`, with specific focus on timeout refund finality.
- Invariant to test: base class id cannot be interpreted as path segments that alter escrow semantics; additionally, source/sink direction must determine escrow, burn, mint, and unescrow consistently.
- Expected Immunefi impact: Critical loss or permanent lock of an escrowed NFT during IBC transfer lifecycle; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: unit and keeper tests over trace parsing plus full SendTransfer/OnRecvPacket round trips; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
