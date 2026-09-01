# Q0325: activation height front-running via `method_id_upgrade_authority_da_public_keys` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling the serialized body encoding, drive `method_id_upgrade_authority_da_public_keys` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the activation height the council intended and the height the circuit finally stores stop being the same, breaking the invariant that an authorised upgrade cannot be pre-empted by a replay?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `method_id_upgrade_authority_da_public_keys`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: the serialized body encoding
- Exploit idea: activation height front-running - reach `method_id_upgrade_authority_da_public_keys` from that entrypoint and force the divergence where the activation height the council intended and the height the circuit finally stores stop being the same; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `initial_batch_proof_method_ids`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an authorised upgrade cannot be pre-empted by a replay
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a signed body early and assert the genuine one still applies
