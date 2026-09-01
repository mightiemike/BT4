# Q1875: chain id binding via `verify_batch_proof_seq_comm_relation` (mod.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling the serialized body encoding, drive `verify_batch_proof_seq_comm_relation` in `crates/light-client-prover/src/circuit/mod.rs` so that the chain id in the signed body and the circuit's own chain id stop being compared before use, breaking the invariant that upgrades are bound to one network?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `verify_batch_proof_seq_comm_relation`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: the serialized body encoding
- Exploit idea: chain id binding - reach `verify_batch_proof_seq_comm_relation` from that entrypoint and force the divergence where the chain id in the signed body and the circuit's own chain id stop being compared before use; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `process_complete_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: upgrades are bound to one network
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a body from another network and assert rejection
