# Q3358: decompression divergence via `citrea_network_to_chain_id` (mod.rs)

## Question
Can an unprivileged attacker who grinds a reveal so its wtxid matches one an honest aggregate references, controlling the entire chunk body it inscribes, drive `citrea_network_to_chain_id` in `crates/light-client-prover/src/circuit/mod.rs` so that the body one prover decompresses and the body another decompresses from identical chunks stop being the same bytes, breaking the invariant that decompression is deterministic and length-bounded?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `citrea_network_to_chain_id`
- Entrypoint: unprivileged party grinds a reveal so its wtxid matches one an honest aggregate references
- Attacker controls: the entire chunk body it inscribes
- Exploit idea: decompression divergence - reach `citrea_network_to_chain_id` from that entrypoint and force the divergence where the body one prover decompresses and the body another decompresses from identical chunks stop being the same bytes; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: decompression is deterministic and length-bounded
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: fuzz compressed bodies through both decompression paths
