# Q0106: circuit input assembled from attacker data via `run` (l2_syncer.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling the commitment range boundaries, drive `run` in `crates/batch-prover/src/l2_syncer.rs` so that the input the prover assembles and the data the DA layer verifiably contains stop being the same, breaking the invariant that circuit input is fully derived from verified DA data?

## Target
- File/function: `crates/batch-prover/src/l2_syncer.rs` -> `run`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: the commitment range boundaries
- Exploit idea: circuit input assembled from attacker data - reach `run` from that entrypoint and force the divergence where the input the prover assembles and the data the DA layer verifiably contains stop being the same; the adjacent symbols in the same file that carry the value are `L2Syncer`, `process_l2_block`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit input is fully derived from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject unverified data into the input builder and assert rejection
