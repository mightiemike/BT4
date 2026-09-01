# Q0410: short header proof requested versus stored via `store_commitments_from_da` (service.rs)

## Question
Can an unprivileged attacker who submits transactions that land in the last L2 block before a sequencer commitment is produced, controlling the L2 height at which its transactions land, drive `store_commitments_from_da` in `crates/sequencer/src/commitment/service.rs` so that the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact, breaking the invariant that every queried L1 hash has a matching stored proof?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `store_commitments_from_da`
- Entrypoint: unprivileged party submits transactions that land in the last L2 block before a sequencer commitment is produced
- Attacker controls: the L2 height at which its transactions land
- Exploit idea: short header proof requested versus stored - reach `store_commitments_from_da` from that entrypoint and force the divergence where the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `get_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every queried L1 hash has a matching stored proof
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: query an unstored hash and assert a defined outcome
