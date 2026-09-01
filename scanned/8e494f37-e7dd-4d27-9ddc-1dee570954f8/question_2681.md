# Q2681: circuit input assembled from attacker data via `get_forks` (batch_proof_bitcoin.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific proved range, controlling the L1 payload the prover must ingest, drive `get_forks` in `guests/risc0/batch-proof/bitcoin/src/bin/batch_proof_bitcoin.rs` so that the input the prover assembles and the data the DA layer verifiably contains stop being the same, breaking the invariant that circuit input is fully derived from verified DA data?

## Target
- File/function: `guests/risc0/batch-proof/bitcoin/src/bin/batch_proof_bitcoin.rs` -> `get_forks`
- Entrypoint: unprivileged party sends L2 transactions that force a specific proved range
- Attacker controls: the L1 payload the prover must ingest
- Exploit idea: circuit input assembled from attacker data - reach `get_forks` from that entrypoint and force the divergence where the input the prover assembles and the data the DA layer verifiably contains stop being the same; the adjacent symbols in the same file that carry the value are `main`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit input is fully derived from verified DA data
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: inject unverified data into the input builder and assert rejection
