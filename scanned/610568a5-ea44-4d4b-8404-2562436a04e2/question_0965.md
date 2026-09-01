# Q0965: unparsable blob handling via `build_from_l1_block` (input_builder.rs)

## Question
Can an unprivileged attacker who times inscriptions so two honest provers observe different blob ordering, controlling the exact byte encoding on the parse boundary, drive `build_from_l1_block` in `crates/light-client-prover/src/input_builder.rs` so that the blob set the circuit skips as unparsable and the set a differently-built node skips stop being the same set, breaking the invariant that parse failure is deterministic across implementations?

## Target
- File/function: `crates/light-client-prover/src/input_builder.rs` -> `build_from_l1_block`
- Entrypoint: unprivileged party times inscriptions so two honest provers observe different blob ordering
- Attacker controls: the exact byte encoding on the parse boundary
- Exploit idea: unparsable blob handling - reach `build_from_l1_block` from that entrypoint and force the divergence where the blob set the circuit skips as unparsable and the set a differently-built node skips stop being the same set; the adjacent symbols in the same file that carry the value are `PreparedLightClientCircuitInput`, `LightClientInputBuilder`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: parse failure is deterministic across implementations
- Expected Immunefi impact: Critical - light client proof split: two honest provers commit different outputs for the same L1 block, attacking Clementine operators
- Fast validation: fuzz near-miss `DataOnDa` encodings and compare skip decisions
