# Q1315: signature index handling via `method_id_upgrade_authority_da_public_keys` (initial_values.rs)

## Question
Can an unprivileged attacker who replays a genuinely council-signed method-id body at a height or chain of its choosing, controlling signature bytes and pubkey indices, drive `method_id_upgrade_authority_da_public_keys` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the pubkey set the signatures are checked against and the distinct council members required stop being the same set, breaking the invariant that three distinct authorised signers are required?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `method_id_upgrade_authority_da_public_keys`
- Entrypoint: unprivileged party replays a genuinely council-signed method-id body at a height or chain of its choosing
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: signature index handling - reach `method_id_upgrade_authority_da_public_keys` from that entrypoint and force the divergence where the pubkey set the signatures are checked against and the distinct council members required stop being the same set; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `initial_batch_proof_method_ids`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: three distinct authorised signers are required
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: submit duplicate/out-of-order/boundary indices and assert rejection
