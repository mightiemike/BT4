# Q0335: chain id binding via `method_id_upgrade_authority_da_public_keys` (initial_values.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling activation height and chain id fields, drive `method_id_upgrade_authority_da_public_keys` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the chain id in the signed body and the circuit's own chain id stop being compared before use, breaking the invariant that upgrades are bound to one network?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `method_id_upgrade_authority_da_public_keys`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: activation height and chain id fields
- Exploit idea: chain id binding - reach `method_id_upgrade_authority_da_public_keys` from that entrypoint and force the divergence where the chain id in the signed body and the circuit's own chain id stop being compared before use; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `initial_batch_proof_method_ids`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: upgrades are bound to one network
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a body from another network and assert rejection
