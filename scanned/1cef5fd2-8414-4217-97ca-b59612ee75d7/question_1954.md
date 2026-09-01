# Q1954: type decoding across versions via `simulate_with_hints` (mod.rs)

## Question
Can an unprivileged attacker who supplies data that a proof output or witness type must round-trip, controlling the version boundary crossed, drive `simulate_with_hints` in `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` so that the value written under one type version and the value read under another stop being the same, breaking the invariant that stored types decode identically across supported versions?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/mod.rs` -> `simulate_with_hints`
- Entrypoint: unprivileged party supplies data that a proof output or witness type must round-trip
- Attacker controls: the version boundary crossed
- Exploit idea: type decoding across versions - reach `simulate_with_hints` from that entrypoint and force the divergence where the value written under one type version and the value read under another stop being the same; the adjacent symbols in the same file that carry the value are `LocalProvingSessionInfo`, `BonsaiProvingSessionInfo`, `BoundlessProvingSessionInfo`, `ProvingSessionInfo`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored types decode identically across supported versions
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: round-trip across a fork boundary
