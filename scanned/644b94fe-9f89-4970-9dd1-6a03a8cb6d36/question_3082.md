# Q3082: short header proof requested versus stored via `get_mined_commitments_from` (service.rs)

## Question
Can an unprivileged attacker who sends transactions sized to sit exactly at the commitment blob size threshold, controlling transaction sizes at the blob threshold, drive `get_mined_commitments_from` in `crates/sequencer/src/commitment/service.rs` so that the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact, breaking the invariant that every queried L1 hash has a matching stored proof?

## Target
- File/function: `crates/sequencer/src/commitment/service.rs` -> `get_mined_commitments_from`
- Entrypoint: unprivileged party sends transactions sized to sit exactly at the commitment blob size threshold
- Attacker controls: transaction sizes at the blob threshold
- Exploit idea: short header proof requested versus stored - reach `get_mined_commitments_from` from that entrypoint and force the divergence where the short header proof the sequencer stored for an L1 hash and the proof the prover later needs stop being the same artefact; the adjacent symbols in the same file that carry the value are `CommitmentService`, `run`, `commit`, `store_commitments_from_da`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every queried L1 hash has a matching stored proof
- Expected Immunefi impact: Critical - a true state transition made permanently unprovable, halting settlement and bridge withdrawals
- Fast validation: query an unstored hash and assert a defined outcome
