# Q2993: journal output versioning via `add_hint` (mod.rs)

## Question
Can an unprivileged attacker who supplies data that a proof output or witness type must round-trip, controlling the encoded output or witness bytes, drive `add_hint` in `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` so that the output version a proof commits and the version the verifier assumes stop being the same, breaking the invariant that outputs are self-describing and version-checked?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` -> `add_hint`
- Entrypoint: unprivileged party supplies data that a proof output or witness type must round-trip
- Attacker controls: the encoded output or witness bytes
- Exploit idea: journal output versioning - reach `add_hint` from that entrypoint and force the divergence where the output version a proof commits and the version the verifier assumes stop being the same; the adjacent symbols in the same file that carry the value are `LocalProvingSessionInfo`, `BonsaiProvingSessionInfo`, `BoundlessProvingSessionInfo`, `ProvingSessionInfo`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: outputs are self-describing and version-checked
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a v3 output as another version and assert rejection
