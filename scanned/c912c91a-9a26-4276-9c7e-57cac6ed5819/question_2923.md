# Q2923: witness serialization drift via `get_hints` (witness.rs)

## Question
Can an unprivileged attacker who drives a stored proof output across a version boundary, controlling the encoded output or witness bytes, drive `get_hints` in `crates/sovereign-sdk/rollup-interface/src/state_machine/witness.rs` so that the witness a native run serialises and the witness a guest deserialises stop being the same object, breaking the invariant that witness encoding is canonical?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/witness.rs` -> `get_hints`
- Entrypoint: unprivileged party drives a stored proof output across a version boundary
- Attacker controls: the encoded output or witness bytes
- Exploit idea: witness serialization drift - reach `get_hints` from that entrypoint and force the divergence where the witness a native run serialises and the witness a guest deserialises stop being the same object; the adjacent symbols in the same file that carry the value are `Witness`, `add_hint`, `get_hint`, `remaining`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: witness encoding is canonical
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: fuzz witness encodings and assert strict decoding
