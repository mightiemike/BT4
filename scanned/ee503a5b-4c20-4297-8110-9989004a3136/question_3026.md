# Q3026: commitment index continuity via `commit` (service.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling the L2 height at which its transactions land, drive `commit` in `crates/sequencer/src/commitment/service.rs` so that the commitment index the sequencer emits and the index the light client expects next stop being consecutive, breaking the invariant that commitment indices form a gapless chain?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `commit`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: commitment index continuity - reach `commit` from that entrypoint and force the divergence where the commitment index the sequencer emits and the index the light client expects next stop being consecutive; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `store_commitments_from_da`, `get_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commitment indices form a gapless chain
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: emit around a gap and assert the light client refuses to advance
