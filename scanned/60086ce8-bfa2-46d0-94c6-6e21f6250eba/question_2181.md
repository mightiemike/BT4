# Q2181: recursion/aggregation boundary via `verify_and_deserialize_output` (guest.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the overlap between requested ranges, drive `verify_and_deserialize_output` in `crates/risc0/src/guest.rs` so that the set of sub-proofs aggregated and the set the output claims stop being the same set, breaking the invariant that aggregation is complete and exact?

## Target
- File/function: `crates/risc0/src/guest.rs` -> `verify_and_deserialize_output`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the overlap between requested ranges
- Exploit idea: recursion/aggregation boundary - reach `verify_and_deserialize_output` from that entrypoint and force the divergence where the set of sub-proofs aggregated and the set the output claims stop being the same set; the adjacent symbols in the same file that carry the value are `Risc0Guest`, `Risc0GuestError`, `read_from_host`, `commit`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: aggregation is complete and exact
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: drop a sub-proof and assert the aggregate fails
