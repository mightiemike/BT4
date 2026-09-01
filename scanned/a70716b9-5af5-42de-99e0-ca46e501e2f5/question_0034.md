# Q0034: activation height front-running via `initial_batch_proof_method_ids` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling signature bytes and pubkey indices, drive `initial_batch_proof_method_ids` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the activation height the council intended and the height the circuit finally stores stop being the same, breaking the invariant that an authorised upgrade cannot be pre-empted by a replay?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `initial_batch_proof_method_ids`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: activation height front-running - reach `initial_batch_proof_method_ids` from that entrypoint and force the divergence where the activation height the council intended and the height the circuit finally stores stop being the same; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `batch_prover_da_public_key`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: an authorised upgrade cannot be pre-empted by a replay
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: replay a signed body early and assert the genuine one still applies
