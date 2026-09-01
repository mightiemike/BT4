# Q1015: chain id binding via `get_l2_genesis_root` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling the serialized body encoding, drive `get_l2_genesis_root` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the chain id in the signed body and the circuit's own chain id stop being compared before use, breaking the invariant that upgrades are bound to one network?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `get_l2_genesis_root`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: the serialized body encoding
- Exploit idea: chain id binding - reach `get_l2_genesis_root` from that entrypoint and force the divergence where the chain id in the signed body and the circuit's own chain id stop being compared before use; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `initial_batch_proof_method_ids`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: upgrades are bound to one network
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a body from another network and assert rejection
