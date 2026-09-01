# Q3193: codec round-trip mismatch via `try_decode_value` (rlp_codec.rs)

## Question
Can an unprivileged attacker who stores attacker-chosen values through a contract so they reach a module container, controlling the encoded value written, drive `try_decode_value` in `crates/sovereign-sdk/module-system/sov-state/src/codec/rlp_codec.rs` so that the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping, breaking the invariant that storage codecs are injective?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/codec/rlp_codec.rs` -> `try_decode_value`
- Entrypoint: unprivileged party stores attacker-chosen values through a contract so they reach a module container
- Attacker controls: the encoded value written
- Exploit idea: codec round-trip mismatch - reach `try_decode_value` from that entrypoint and force the divergence where the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping; the adjacent symbols in the same file that carry the value are `RlpCodec`, `encode_key`, `encode_value`, `key_codec`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: storage codecs are injective
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fuzz encode/decode for every stored type
