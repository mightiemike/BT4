# Q1435: initial values pinning via `get_l2_genesis_root` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling signature bytes and pubkey indices, drive `get_l2_genesis_root` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the constants the circuit compiles with and the constants the network deployed stop being the same, breaking the invariant that circuit constants match deployment?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `get_l2_genesis_root`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: initial values pinning - reach `get_l2_genesis_root` from that entrypoint and force the divergence where the constants the circuit compiles with and the constants the network deployed stop being the same; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `initial_batch_proof_method_ids`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: circuit constants match deployment
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: diff compiled constants against the deployed configuration
