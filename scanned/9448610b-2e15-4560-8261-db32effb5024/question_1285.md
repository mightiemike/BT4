# Q1285: sender check applied to the wrong field via `build_from_l1_block` (input_builder.rs)

## Question
Can an unprivileged attacker who inscribes near-miss encodings that sit on the parse/skip boundary, controlling how many blobs land in one block, drive `build_from_l1_block` in `crates/light-client-prover/src/input_builder.rs` so that the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key, breaking the invariant that prover-authored blobs are authenticated before use?

## Target
- File/function: `crates/light-client-prover/src/input_builder.rs` -> `build_from_l1_block`
- Entrypoint: unprivileged party inscribes near-miss encodings that sit on the parse/skip boundary
- Attacker controls: how many blobs land in one block
- Exploit idea: sender check applied to the wrong field - reach `build_from_l1_block` from that entrypoint and force the divergence where the key compared against `batch_prover_da_public_key` and the key that authorised the blob stop being the same key; the adjacent symbols in the same file that carry the value are `PreparedLightClientCircuitInput`, `LightClientInputBuilder`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: prover-authored blobs are authenticated before use
- Expected Immunefi impact: Critical - unauthorized sequencer commitment / method-id upgrade accepted without the signing authority
- Fast validation: inscribe with a lookalike script and assert the sender check fails closed
