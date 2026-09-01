# Q2712: state transition chaining loop via `process_complete_proof` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling which commitment indices are covered, drive `process_complete_proof` in `crates/light-client-prover/src/circuit/mod.rs` so that the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root, breaking the invariant that chaining only advances on matching initial roots?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `process_complete_proof`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: which commitment indices are covered
- Exploit idea: state transition chaining loop - reach `process_complete_proof` from that entrypoint and force the divergence where the state root the chaining loop advances to and the root the batch proof for that index proved stop being the same root; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: chaining only advances on matching initial roots
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: offer a proof with a mismatched initial root and assert no advance
