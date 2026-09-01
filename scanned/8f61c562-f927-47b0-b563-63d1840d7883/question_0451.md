# Q0451: circuit input assembled from attacker data via `start_proving` (prover.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling which L1 hash a short header proof is requested for, drive `start_proving` in `crates/batch-prover/src/prover.rs` so that the input the prover assembles and the data the DA layer verifiably contains stop being the same, breaking the invariant that circuit input is fully derived from verified DA data?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `start_proving`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: which L1 hash a short header proof is requested for
- Exploit idea: circuit input assembled from attacker data - reach `start_proving` from that entrypoint and force the divergence where the input the prover assembles and the data the DA layer verifiably contains stop being the same; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit input is fully derived from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject unverified data into the input builder and assert rejection
