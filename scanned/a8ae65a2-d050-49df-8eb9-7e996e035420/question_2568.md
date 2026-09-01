# Q2568: sender check applied to the wrong field via `verify_batch_proof_seq_comm_relation` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path, controlling how many blobs land in one block, drive `verify_batch_proof_seq_comm_relation` in `crates/light-client-prover/src/circuit/mod.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `verify_batch_proof_seq_comm_relation`
- Entrypoint: unprivileged party inscribes a blob that fails to deserialise as `DataOnDa` and forces the `continue` path
- Attacker controls: how many blobs land in one block
- Exploit idea: sender check applied to the wrong field - reach `verify_batch_proof_seq_comm_relation` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `process_complete_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
