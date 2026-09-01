# Q1834: type decoding across versions via `BatchProofCircuitOutputV3` (v3.rs)

## Question
Can an unprivileged attacker who drives a stored proof output across a version boundary, controlling the encoded output or witness bytes, drive `BatchProofCircuitOutputV3` in `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/output/v3.rs` so that the value written under one type version and the value read under another stop being the same, breaking the invariant that stored types decode identically across supported versions?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/zk/batch_proof/output/v3.rs` -> `BatchProofCircuitOutputV3`
- Entrypoint: unprivileged party drives a stored proof output across a version boundary
- Attacker controls: the encoded output or witness bytes
- Exploit idea: type decoding across versions - reach `BatchProofCircuitOutputV3` from that entrypoint and force the divergence where the value written under one type version and the value read under another stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: stored types decode identically across supported versions
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: round-trip across a fork boundary
