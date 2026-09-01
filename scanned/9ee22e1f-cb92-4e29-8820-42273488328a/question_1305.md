# Q1305: method-id upgrade without authority via `method_id_upgrade_authority_da_public_keys` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling activation height and chain id fields, drive `method_id_upgrade_authority_da_public_keys` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value, breaking the invariant that method ids change only by authorised upgrade?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `method_id_upgrade_authority_da_public_keys`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: activation height and chain id fields
- Exploit idea: method-id upgrade without authority - reach `method_id_upgrade_authority_da_public_keys` from that entrypoint and force the divergence where the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `initial_batch_proof_method_ids`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: method ids change only by authorised upgrade
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe a crafted body and assert `verify_method_id_security_council` rejects it
