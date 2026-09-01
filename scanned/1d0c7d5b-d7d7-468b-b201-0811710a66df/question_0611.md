# Q0611: circuit input assembled from attacker data via `get_batch_proof_circuit_input_from_commitments` (prover.rs)

## Question
Can an unprivileged attacker who forces the prover to request a short header proof for an L1 hash of its choosing, controlling the commitment range boundaries, drive `get_batch_proof_circuit_input_from_commitments` in `crates/batch-prover/src/prover.rs` so that the input the prover assembles and the data the DA layer verifiably contains stop being the same, breaking the invariant that circuit input is fully derived from verified DA data?

## Target
- File/function: `crates/batch-prover/src/prover.rs` -> `get_batch_proof_circuit_input_from_commitments`
- Entrypoint: unprivileged party forces the prover to request a short header proof for an L1 hash of its choosing
- Attacker controls: the commitment range boundaries
- Exploit idea: circuit input assembled from attacker data - reach `get_batch_proof_circuit_input_from_commitments` from that entrypoint and force the divergence where the input the prover assembles and the data the DA layer verifiably contains stop being the same; the adjacent symbols in the same file that carry the value are `ProverRequest`, `Prover`, `CommitmentStateTransitionData`, `run`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit input is fully derived from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject unverified data into the input builder and assert rejection
