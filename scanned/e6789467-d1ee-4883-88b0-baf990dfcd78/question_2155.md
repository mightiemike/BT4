# Q2155: chain id binding via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling the serialized body encoding, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the chain id in the signed body and the circuit's own chain id stop being compared before use, breaking the invariant that upgrades are bound to one network?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: the serialized body encoding
- Exploit idea: chain id binding - reach `run_l1_block` from that entrypoint and force the divergence where the chain id in the signed body and the circuit's own chain id stop being compared before use; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: upgrades are bound to one network
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a body from another network and assert rejection
