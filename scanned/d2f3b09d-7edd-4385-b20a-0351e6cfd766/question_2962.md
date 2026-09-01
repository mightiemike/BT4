# Q2962: accessory/offchain state leaking into root via `key_codec` (borsh_codec.rs)

## Question
Can an unprivileged attacker who drives a stored value across an encoding boundary, controlling the key under which it is stored, drive `key_codec` in `crates/sovereign-sdk/module-system/sov-state/src/codec/borsh_codec.rs` so that the keys included in the state root and the keys the protocol declares as accessory stop being disjoint, breaking the invariant that accessory state never affects the proved root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/codec/borsh_codec.rs` -> `key_codec`
- Entrypoint: unprivileged party drives a stored value across an encoding boundary
- Attacker controls: the key under which it is stored
- Exploit idea: accessory/offchain state leaking into root - reach `key_codec` from that entrypoint and force the divergence where the keys included in the state root and the keys the protocol declares as accessory stop being disjoint; the adjacent symbols in the same file that carry the value are `BorshCodec`, `encode_key`, `encode_value`, `try_decode_value`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: accessory state never affects the proved root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write accessory state and assert the root is unchanged
