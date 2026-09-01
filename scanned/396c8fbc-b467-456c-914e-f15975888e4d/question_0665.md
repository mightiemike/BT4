# Q0665: method-id upgrade without authority via `initial_batch_proof_method_ids` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling signature bytes and pubkey indices, drive `initial_batch_proof_method_ids` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value, breaking the invariant that method ids change only by authorised upgrade?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `initial_batch_proof_method_ids`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: method-id upgrade without authority - reach `initial_batch_proof_method_ids` from that entrypoint and force the divergence where the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: method ids change only by authorised upgrade
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe a crafted body and assert `verify_method_id_security_council` rejects it
