# Q0012: method-id upgrade without authority via `get_l2_genesis_root` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling signature bytes and pubkey indices, drive `get_l2_genesis_root` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value, breaking the invariant that method ids change only by authorised upgrade?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `get_l2_genesis_root`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: method-id upgrade without authority - reach `get_l2_genesis_root` from that entrypoint and force the divergence where the method id inserted by `BatchProofMethodIdAccessor` and the id three council keys signed stop being the same value; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `initial_batch_proof_method_ids`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: method ids change only by authorised upgrade
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe a crafted body and assert `verify_method_id_security_council` rejects it
