# Q4549: commitment index continuity via `load_next_commitment_index_and_start_height` (helpers.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling the L2 height at which its transactions land, drive `load_next_commitment_index_and_start_height` in `crates/sequencer/src/commitment/helpers.rs` so that the commitment index the sequencer emits and the index the light client expects next stop being consecutive, breaking the invariant that commitment indices form a gapless chain?

## Target
- File/function: `crates/sequencer/src/commitment/helpers.rs` -> `load_next_commitment_index_and_start_height`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment index continuity - reach `load_next_commitment_index_and_start_height` from that entrypoint and force the divergence where the commitment index the sequencer emits and the index the light client expects next stop being consecutive; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment indices form a gapless chain
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: emit around a gap and assert the light client refuses to advance
