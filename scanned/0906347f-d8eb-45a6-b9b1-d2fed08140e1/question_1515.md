# Q1515: stale previous-proof reuse via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the chunk/aggregate graph it plants, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same, breaking the invariant that each LCP chains to its exact predecessor?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: stale previous-proof reuse - reach `run_l1_block` from that entrypoint and force the divergence where the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each LCP chains to its exact predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: chain from an older output and assert rejection
