# Q1019: accessory/offchain state leaking into root via `with_codec` (vec.rs)

## Question
Can an unprivileged attacker who stores attacker-chosen values through a contract so they reach a module container, controlling the encoded value written, drive `with_codec` in `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/vec.rs` so that the keys included in the state root and the keys the protocol declares as accessory stop being disjoint, breaking the invariant that accessory state never affects the proved root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/vec.rs` -> `with_codec`
- Entrypoint: unprivileged party stores attacker-chosen values through a contract so they reach a module container
- Attacker controls: the encoded value written
- Exploit idea: accessory/offchain state leaking into root - reach `with_codec` from that entrypoint and force the divergence where the keys included in the state root and the keys the protocol declares as accessory stop being disjoint; the adjacent symbols in the same file that carry the value are `StateVec`, `set_len`, `elems`, `len_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: accessory state never affects the proved root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write accessory state and assert the root is unchanged
