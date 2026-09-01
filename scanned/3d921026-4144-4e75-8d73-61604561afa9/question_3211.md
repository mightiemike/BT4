# Q3211: decompression divergence via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who inscribes chunk wtxids that an honest aggregate will later dereference, controlling the order in which chunks land in the block, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the body one prover decompresses and the body another decompresses from identical chunks stop being the same bytes, breaking the invariant that decompression is deterministic and length-bounded?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party inscribes chunk wtxids that an honest aggregate will later dereference
- Attacker controls: the order in which chunks land in the block
- Exploit idea: decompression divergence - reach `run_l1_block` from that entrypoint and force the divergence where the body one prover decompresses and the body another decompresses from identical chunks stop being the same bytes; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: decompression is deterministic and length-bounded
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: fuzz compressed bodies through both decompression paths
