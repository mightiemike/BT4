# Q3190: prior-output carry-over via `run_l1_block` (mod.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the initial and final roots the offered data claims, drive `run_l1_block` in `crates/light-client-prover/src/circuit/mod.rs` so that the previous output the circuit assumes and the previous output actually produced stop being the same journal, breaking the invariant that each proof chains to its true predecessor?

## Target
- File/function: `crates/light-client-prover/src/circuit/mod.rs` -> `run_l1_block`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: prior-output carry-over - reach `run_l1_block` from that entrypoint and force the divergence where the previous output the circuit assumes and the previous output actually produced stop being the same journal; the adjacent symbols in the same file that carry the value are `LightClientVerificationError`, `RunL1BlockResult`, `LightClientProofCircuit`, `verify_batch_proof_seq_comm_relation`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof chains to its true predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a mismatched previous output and assert rejection
