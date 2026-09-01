# Q2395: prior-output carry-over via `build_services` (services.rs)

## Question
Can an unprivileged attacker who arranges the L1 data so the chaining loop is offered a mismatched initial root, controlling the chunk/aggregate graph it plants, drive `build_services` in `crates/light-client-prover/src/services.rs` so that the previous output the circuit assumes and the previous output actually produced stop being the same journal, breaking the invariant that each proof chains to its true predecessor?

## Target
- File/function: `crates/light-client-prover/src/services.rs` -> `build_services`
- Entrypoint: unprivileged party arranges the L1 data so the chaining loop is offered a mismatched initial root
- Attacker controls: the chunk/aggregate graph it plants
- Exploit idea: prior-output carry-over - reach `build_services` from that entrypoint and force the divergence where the previous output the circuit assumes and the previous output actually produced stop being the same journal; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: each proof chains to its true predecessor
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: feed a mismatched previous output and assert rejection
