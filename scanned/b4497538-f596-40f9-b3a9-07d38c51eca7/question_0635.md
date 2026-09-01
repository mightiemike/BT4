# Q0635: skip-path determinism via `build_from_l1_block` (input_builder.rs)

## Question
Can an unprivileged attacker who times inscriptions so two honest provers observe different blob ordering, controlling how many blobs land in one block, drive `build_from_l1_block` in `crates/light-client-prover/src/input_builder.rs` so that the journal produced when a blob is skipped by `continue` and the journal another prover produces stop being the same, breaking the invariant that every skip decision is a pure function of the blob and prior state?

## Target
- File/function: `crates/light-client-prover/src/input_builder.rs` -> `build_from_l1_block`
- Entrypoint: unprivileged party times inscriptions so two honest provers observe different blob ordering
- Attacker controls: how many blobs land in one block
- Exploit idea: skip-path determinism - reach `build_from_l1_block` from that entrypoint and force the divergence where the journal produced when a blob is skipped by `continue` and the journal another prover produces stop being the same; the adjacent symbols in the same file that carry the value are `PreparedLightClientCircuitInput`, `LightClientInputBuilder`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every skip decision is a pure function of the blob and prior state
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: run two provers with different ingestion orders and diff outputs
