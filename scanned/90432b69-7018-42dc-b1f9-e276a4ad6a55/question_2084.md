# Q2084: witness serialization drift via `verify_and_deserialize_output` (mod.rs)

## Question
Can an unprivileged attacker who drives a stored proof output across a version boundary, controlling the version boundary crossed, drive `verify_and_deserialize_output` in `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` so that the witness a native run serialises and the witness a guest deserialises stop being the same object, breaking the invariant that witness encoding is canonical?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` -> `verify_and_deserialize_output`
- Entrypoint: unprivileged party drives a stored proof output across a version boundary
- Attacker controls: the version boundary crossed
- Exploit idea: witness serialization drift - reach `verify_and_deserialize_output` from that entrypoint and force the divergence where the witness a native run serialises and the witness a guest deserialises stop being the same object; the adjacent symbols in the same file that carry the value are `LocalProvingSessionInfo`, `BonsaiProvingSessionInfo`, `BoundlessProvingSessionInfo`, `ProvingSessionInfo`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: witness encoding is canonical
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: fuzz witness encodings and assert strict decoding
