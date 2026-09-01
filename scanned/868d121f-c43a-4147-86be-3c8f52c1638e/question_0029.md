# Q0029: short header proof provider state via `run` (l1_syncer.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that the batch prover must consume when building circuit input, controlling the L1 payload the prover must ingest, drive `run` in `crates/batch-prover/src/l1_syncer.rs` so that the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal, breaking the invariant that the reported L1 anchor equals the referenced anchor?

## Target
- File/function: `crates/batch-prover/src/l1_syncer.rs` -> `run`
- Entrypoint: unprivileged party inscribes L1 data that the batch prover must consume when building circuit input
- Attacker controls: the L1 payload the prover must ingest
- Exploit idea: short header proof provider state - reach `run` from that entrypoint and force the divergence where the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal; the adjacent symbols in the same file that carry the value are `L1Syncer`, `process_l1_blocks`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the reported L1 anchor equals the referenced anchor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: force a query pattern and diff against `get_last_l1_hash_on_contract`
