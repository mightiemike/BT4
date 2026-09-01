# Q1489: codec round-trip mismatch via `prefix` (vec.rs)

## Question
Can an unprivileged attacker who drives a stored value across an encoding boundary, controlling the key under which it is stored, drive `prefix` in `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/vec.rs` so that the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping, breaking the invariant that storage codecs are injective?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-api/src/containers/vec.rs` -> `prefix`
- Entrypoint: unprivileged party drives a stored value across an encoding boundary
- Attacker controls: the key under which it is stored
- Exploit idea: codec round-trip mismatch - reach `prefix` from that entrypoint and force the divergence where the bytes a value encodes to on write and the bytes it decodes from on read stop round-tripping; the adjacent symbols in the same file that carry the value are `StateVec`, `with_codec`, `set_len`, `elems`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: storage codecs are injective
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: fuzz encode/decode for every stored type
