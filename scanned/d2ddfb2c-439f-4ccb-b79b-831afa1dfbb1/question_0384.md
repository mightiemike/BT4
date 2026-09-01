# Q0384: journal output versioning via `timestamp` (block.rs)

## Question
Can an unprivileged attacker who supplies data that a proof output or witness type must round-trip, controlling the version boundary crossed, drive `timestamp` in `crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs` so that the output version a proof commits and the version the verifier assumes stop being the same, breaking the invariant that outputs are self-describing and version-checked?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs` -> `timestamp`
- Entrypoint: unprivileged party supplies data that a proof output or witness type must round-trip
- Attacker controls: the version boundary crossed
- Exploit idea: journal output versioning - reach `timestamp` from that entrypoint and force the divergence where the output version a proof commits and the version the verifier assumes stop being the same; the adjacent symbols in the same file that carry the value are `L2Header`, `SignedL2Header`, `L2Block`, `state_root`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: outputs are self-describing and version-checked
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a v3 output as another version and assert rejection
