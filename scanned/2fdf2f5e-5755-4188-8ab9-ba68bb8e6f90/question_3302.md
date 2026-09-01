# Q3302: journal hash preimage binding via `process_complete_proof` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the chunk/aggregate graph it plants, drive `process_complete_proof` in `crates/light-client-prover/src/circuit/mod.rs` so that the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple, breaking the invariant that journal commitment covers every field a consumer trusts?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `process_complete_proof`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: journal hash preimage binding - reach `process_complete_proof` from that entrypoint and force the divergence where the fields hashed into the committed journal and the fields the verifier re-derives stop being the same tuple; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: journal commitment covers every field a consumer trusts
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: mutate an uncommitted field and assert the journal changes
