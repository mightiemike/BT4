# Q0381: short header proof provider state via `partition_commitments` (prover.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling which L1 hash a short header proof is requested for, drive `partition_commitments` in `crates/batch-prover/src/prover.rs` so that the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal, breaking the invariant that the reported L1 anchor equals the referenced anchor?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `partition_commitments`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: short header proof provider state - reach `partition_commitments` from that entrypoint and force the divergence where the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the reported L1 anchor equals the referenced anchor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: force a query pattern and diff against `get_last_l1_hash_on_contract`
