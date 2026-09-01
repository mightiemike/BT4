# Q2519: short header proof provider state via `get_prev_hash_proof` (prover.rs)

## Question
Can an unprivileged attacker who forces the prover to request a short header proof for an L1 hash of its choosing, controlling the commitment range boundaries, drive `get_prev_hash_proof` in `crates/batch-prover/src/prover.rs` so that the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal, breaking the invariant that the reported L1 anchor equals the referenced anchor?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `get_prev_hash_proof`
- Entrypoint: unprivileged party forces the prover to request a short header proof for an L1 hash of its choosing
- Attacker controls: the commitment range boundaries
- Exploit idea: short header proof provider state - reach `get_prev_hash_proof` from that entrypoint and force the divergence where the L1 hash reported as last-queried and the hash the proved blocks actually referenced stop being equal; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: the reported L1 anchor equals the referenced anchor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: force a query pattern and diff against `get_last_l1_hash_on_contract`
