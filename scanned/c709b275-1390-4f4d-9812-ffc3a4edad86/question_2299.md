# Q2299: accessory/offchain state leaking into root via `encode_key_like` (codec.rs)

## Question
Can an unprivileged attacker who drives a stored value across an encoding boundary, controlling the encoded value written, drive `encode_key_like` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/codec.rs` so that the keys included in the state root and the keys the protocol declares as accessory stop being disjoint, breaking the invariant that accessory state never affects the proved root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/codec.rs` -> `encode_key_like`
- Entrypoint: unprivileged party drives a stored value across an encoding boundary
- Attacker controls: the encoded value written
- Exploit idea: accessory/offchain state leaking into root - reach `encode_key_like` from that entrypoint and force the divergence where the keys included in the state root and the keys the protocol declares as accessory stop being disjoint; the adjacent symbols in the same file that carry the value are `StateValueCodec`, `StateKeyCodec`, `StateCodec`, `EncodeKeyLike`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: accessory state never affects the proved root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write accessory state and assert the root is unchanged
