# Q2889: chain id binding via `citrea_network_to_chain_id` (mod.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling activation height and chain id fields, drive `citrea_network_to_chain_id` in `crates/light-client-prover/src/circuit/mod.rs` so that the chain id in the signed body and the circuit's own chain id stop being compared before use, breaking the invariant that upgrades are bound to one network?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `citrea_network_to_chain_id`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: activation height and chain id fields
- Exploit idea: chain id binding - reach `citrea_network_to_chain_id` from that entrypoint and force the divergence where the chain id in the signed body and the circuit's own chain id stop being compared before use; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: upgrades are bound to one network
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a body from another network and assert rejection
