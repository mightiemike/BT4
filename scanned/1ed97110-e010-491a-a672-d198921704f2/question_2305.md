# Q2305: stale previous-proof reuse via `lib` (lib.rs)

## Question
Can an unprivileged attacker who inscribes commitments and proofs that induce a gap in the verified chain, controlling the chunk/aggregate graph it plants, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same, breaking the invariant that each LCP chains to its exact predecessor?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party inscribes commitments and proofs that induce a gap in the verified chain
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: stale previous-proof reuse - reach `lib` from that entrypoint and force the divergence where the previous LCP output a prover chains from and the output for the immediately preceding L1 block stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each LCP chains to its exact predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: chain from an older output and assert rejection
