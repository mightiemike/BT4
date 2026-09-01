# Q4729: commitment index continuity via `get_finalized_block` (da.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling the L2 height at which its transactions land, drive `get_finalized_block` in `crates/sequencer/src/da.rs` so that the commitment index the sequencer emits and the index the light client expects next stop being consecutive, breaking the invariant that commitment indices form a gapless chain?

## Target
- File/function: `crates/sequencer/src/da.rs` -> `get_finalized_block`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment index continuity - reach `get_finalized_block` from that entrypoint and force the divergence where the commitment index the sequencer emits and the index the light client expects next stop being consecutive; the adjacent symbols in the same file that carry the value are `da_block_monitor`, `fee_rate_monitor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment indices form a gapless chain
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: emit around a gap and assert the light client refuses to advance
