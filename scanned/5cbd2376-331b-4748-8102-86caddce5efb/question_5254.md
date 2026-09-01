# Q5254: commitment range off-by-one via `get_finalized_block` (da.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling reorg depth achievable with valid Bitcoin transactions, drive `get_finalized_block` in `crates/sequencer/src/da.rs` so that the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range, breaking the invariant that a commitment's root commits exactly its declared range?

## Target
- File/function: `crates/sequencer/src/da.rs` -> `get_finalized_block`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: reorg depth achievable with valid Bitcoin transactions
- Exploit idea: commitment range off-by-one - reach `get_finalized_block` from that entrypoint and force the divergence where the L2 height range a sequencer commitment covers and the range its merkle root was built from stop being the same range; the adjacent symbols in the same file that carry the value are `da_block_monitor`, `fee_rate_monitor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a commitment's root commits exactly its declared range
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: build a commitment at a range edge and re-derive its root
