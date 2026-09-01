# Q1131: recursion/aggregation boundary via `commit` (guest.rs)

## Question
Can an unprivileged attacker who makes the proved range span a fork or method-id activation boundary, controlling the activation boundary the range spans, drive `commit` in `crates/risc0/src/guest.rs` so that the set of sub-proofs aggregated and the set the output claims stop being the same set, breaking the invariant that aggregation is complete and exact?

## Target
- File/function: `crates/risc0/src/guest.rs` -> `commit`
- Entrypoint: unprivileged party makes the proved range span a fork or method-id activation boundary
- Attacker controls: the activation boundary the range spans
- Exploit idea: recursion/aggregation boundary - reach `commit` from that entrypoint and force the divergence where the set of sub-proofs aggregated and the set the output claims stop being the same set; the adjacent symbols in the same file that carry the value are `Risc0Guest`, `Risc0GuestError`, `read_from_host`, `verify_with_assumptions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: aggregation is complete and exact
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: drop a sub-proof and assert the aggregate fails
