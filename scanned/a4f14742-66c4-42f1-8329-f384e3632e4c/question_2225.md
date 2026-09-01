# Q2225: prior-output carry-over via `lib` (lib.rs)

## Question
Can an unprivileged attacker who inscribes a complete proof body that decompresses differently than it was chunked, controlling the chunk/aggregate graph it plants, drive `lib` in `crates/light-client-prover/src/lib.rs` so that the previous output the circuit assumes and the previous output actually produced stop being the same journal, breaking the invariant that each proof chains to its true predecessor?

## Target
- File/function: `crates/light-client-prover/src/lib.rs` -> `lib`
- Entrypoint: unprivileged party inscribes a complete proof body that decompresses differently than it was chunked
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: prior-output carry-over - reach `lib` from that entrypoint and force the divergence where the previous output the circuit assumes and the previous output actually produced stop being the same journal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof chains to its true predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a mismatched previous output and assert rejection
