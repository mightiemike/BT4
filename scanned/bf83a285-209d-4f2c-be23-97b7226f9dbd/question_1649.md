# Q1649: codec round-trip mismatch via `encode_value` (codec.rs)

## Question
Can an unprivileged attacker who drives a stored value across an encoding boundary, controlling the key under which it is stored, drive `encode_value` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/codec.rs` so that the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping, breaking the invariant that storage codecs are injective?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/codec.rs` -> `encode_value`
- Entrypoint: unprivileged party drives a stored value across an encoding boundary
- Attacker controls: the key under which it is stored
- Exploit idea: codec round-trip mismatch - reach `encode_value` from that entrypoint and force the divergence where the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping; the adjacent symbols in the same file that carry the value are `StateValueCodec`, `StateKeyCodec`, `StateCodec`, `EncodeKeyLike`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: storage codecs are injective
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fuzz encode/decode for every stored type
