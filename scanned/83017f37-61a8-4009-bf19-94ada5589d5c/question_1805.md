# Q1805: method-id list ordering via `sequencer_da_public_key` (initial_values.rs)

## Question
Can an unprivileged attacker who inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices, controlling signature bytes and pubkey indices, drive `sequencer_da_public_key` in `crates/light-client-prover/src/circuit/initial_values.rs` so that the activation list order the accessor stores and the order lookups assume stop being the same, breaking the invariant that activation lookups are monotone in height?

## Target
- File/function: `crates/light-client-prover/src/circuit/initial_values.rs` -> `sequencer_da_public_key`
- Entrypoint: unprivileged party inscribes a `DataOnDa::BatchProofMethodId` body from an unknown key with chosen signature indices
- Attacker controls: signature bytes and pubkey indices
- Exploit idea: method-id list ordering - reach `sequencer_da_public_key` from that entrypoint and force the divergence where the activation list order the accessor stores and the order lookups assume stop being the same; the adjacent symbols in the same file that carry the value are `InitialValueProvider`, `NonEmptySlice`, `get_l2_genesis_root`, `initial_batch_proof_method_ids`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: activation lookups are monotone in height
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: insert out-of-order activations and assert lookup correctness
