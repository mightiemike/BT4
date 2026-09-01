# Q2438: short header proof provider state via `filter_commitments_with_index_gap` (prover.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling the L1 payload the prover must ingest, drive `filter_commitments_with_index_gap` in `crates/batch-prover/src/prover.rs` so that the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal, breaking the invariant that the reported L1 anchor equals the referenced anchor?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `filter_commitments_with_index_gap`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: the L1 payload the prover must ingest
- Exploit idea: short header proof provider state - reach `filter_commitments_with_index_gap` from that entrypoint and force the divergence where the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the reported L1 anchor equals the referenced anchor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: force a query pattern and diff against `get_last_l1_hash_on_contract`
