# Q2264: type decoding across versions via `signature` (block.rs)

## Question
Can an unprivileged attacker who drives a stored proof output across a version boundary, controlling the version boundary crossed, drive `signature` in `crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs` so that the value written under one type version and the value read under another stop being the same, breaking the invariant that stored types decode identically across supported versions?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/block.rs` -> `signature`
- Entrypoint: unprivileged party drives a stored proof output across a version boundary
- Attacker controls: the version boundary crossed
- Exploit idea: type decoding across versions - reach `signature` from that entrypoint and force the divergence where the value written under one type version and the value read under another stop being the same; the adjacent symbols in the same file that carry the value are `L2Header`, `SignedL2Header`, `L2Block`, `state_root`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored types decode identically across supported versions
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: round-trip across a fork boundary
