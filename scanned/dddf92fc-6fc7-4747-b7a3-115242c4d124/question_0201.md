# Q0201: circuit input assembled from attacker data via `run` (prover.rs)

## Question
Can an unprivileged attacker who inscribes L1 data that the batch prover must consume when building circuit input, controlling the commitment range boundaries, drive `run` in `crates/batch-prover/src/prover.rs` so that the input the prover assembles and the data the DA layer verifiably contains stop being the same, breaking the invariant that circuit input is fully derived from verified DA data?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `run`
- Entrypoint: unprivileged party inscribes L1 data that the batch prover must consume when building circuit input
- Attacker controls: the commitment range boundaries
- Exploit idea: circuit input assembled from attacker data - reach `run` from that entrypoint and force the divergence where the input the prover assembles and the data the DA layer verifiably contains stop being the same; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `try_proving`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit input is fully derived from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject unverified data into the input builder and assert rejection
