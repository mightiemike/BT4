# Q3046: accessory/offchain state leaking into root via `encode_key` (rlp_codec.rs)

## Question
Can an unprivileged attacker who stores attacker-chosen values through a contract so they reach a module container, controlling the key under which it is stored, drive `encode_key` in `crates/sovereign-sdk/module-system/sov-state/src/codec/rlp_codec.rs` so that the keys included in the state root and the keys the protocol declares as accessory stop being disjoint, breaking the invariant that accessory state never affects the proved root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/codec/rlp_codec.rs` -> `encode_key`
- Entrypoint: unprivileged party stores attacker-chosen values through a contract so they reach a module container
- Attacker controls: the key under which it is stored
- Exploit idea: accessory/offchain state leaking into root - reach `encode_key` from that entrypoint and force the divergence where the keys included in the state root and the keys the protocol declares as accessory stop being disjoint; the adjacent symbols in the same file that carry the value are `RlpCodec`, `encode_value`, `try_decode_value`, `key_codec`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: accessory state never affects the proved root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: write accessory state and assert the root is unchanged
