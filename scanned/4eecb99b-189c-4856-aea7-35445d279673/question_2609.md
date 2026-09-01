# Q2609: journal field packing via `filter_commitments_with_index_gap` (prover.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling which L1 hash a short header proof is requested for, drive `filter_commitments_with_index_gap` in `crates/batch-prover/src/prover.rs` so that the journal fields the guest commits and the fields the verifier decodes stop being the same layout, breaking the invariant that journal encoding is canonical?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `filter_commitments_with_index_gap`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: journal field packing - reach `filter_commitments_with_index_gap` from that entrypoint and force the divergence where the journal fields the guest commits and the fields the verifier decodes stop being the same layout; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: journal encoding is canonical
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: round-trip journals across versions
