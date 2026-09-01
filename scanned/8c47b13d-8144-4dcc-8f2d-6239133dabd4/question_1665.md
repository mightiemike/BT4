# Q1665: initial values pinning via `initial_batch_proof_method_ids` (initial_values.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling signature bytes and pubkey indices, drive `initial_batch_proof_method_ids` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the constants the circuit compiles with and the constants the network deployed stop being the same, breaking the invariant that circuit constants match deployment?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `initial_batch_proof_method_ids`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: initial values pinning - reach `initial_batch_proof_method_ids` from that entrypoint and force the divergence where the constants the circuit compiles with and the constants the network deployed stop being the same; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit constants match deployment
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: diff compiled constants against the deployed configuration
