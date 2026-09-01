# Q2321: recursion/aggregation boundary via `receipt_from_inner` (lib.rs)

## Question
Can an unprivileged attacker who makes the proved range span a fork or method-id activation boundary, controlling the overlap between requested ranges, drive `receipt_from_inner` in `crates/risc0/src/lib.rs` so that the set of sub-proofs aggregated and the set the output claims stop being the same set, breaking the invariant that aggregation is complete and exact?

## Target
- File/function: `crates/risc0/src/lib.rs` -> `receipt_from_inner`
- Entrypoint: unprivileged party makes the proved range span a fork or method-id activation boundary
- Attacker controls: the overlap between requested ranges
- Exploit idea: recursion/aggregation boundary - reach `receipt_from_inner` from that entrypoint and force the divergence where the set of sub-proofs aggregated and the set the output claims stop being the same set; the adjacent symbols in the same file that carry the value are `RestoreReceiptErr`, `receipt_from_proof`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: aggregation is complete and exact
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: drop a sub-proof and assert the aggregate fails
