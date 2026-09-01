# Q2987: stale previous-proof reuse via `key` (accessors.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the initial and final roots the offered data claims, drive `key` in `crates/light-client-prover/src/circuit/accessors.rs` so that the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same, breaking the invariant that each LCP chains to its exact predecessor?

## Target
- File/function: `crates/light-client-prover/src/circuit/accessors.rs` -> `key`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the initial and final roots the offered data claims
- Exploit idea: stale previous-proof reuse - reach `key` from that entrypoint and force the divergence where the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same; the adjacent symbols in the same file that carry the value are `BlockHashAccessor`, `ChunkAccessor`, `SequencerCommitmentAccessor`, `VerifiedStateTransitionForSequencerCommitmentIndexAccessor`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each LCP chains to its exact predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: chain from an older output and assert rejection
