# Q1485: sender check applied to the wrong field via `process_complete_proof` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path, controlling the exact byte encoding on the parse boundary, drive `process_complete_proof` in `crates/light-client-prover/src/circuit/mod.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `process_complete_proof`
- Entrypoint: unprivileged party inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path
- Attacker controls: the exact byte encoding on the parse boundary
- Exploit idea: sender check applied to the wrong field - reach `process_complete_proof` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
