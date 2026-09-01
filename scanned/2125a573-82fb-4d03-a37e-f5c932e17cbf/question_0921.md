# Q0921: recursion/aggregation boundary via `ProofGenMode` (lib.rs)

## Question
Can an unprivileged attacker who submits load that forces concurrent proving sessions over overlapping ranges, controlling the activation boundary the range spans, drive `ProofGenMode` in `crates/prover-services/src/lib.rs` so that the set of sub-proofs aggregated and the set the output claims stop being the same set, breaking the invariant that aggregation is complete and exact?

## Target
- File/function: `crates/prover-services/src/lib.rs` -> `ProofGenMode`
- Entrypoint: unprivileged party submits load that forces concurrent proving sessions over overlapping ranges
- Attacker controls: the activation boundary the range spans
- Exploit idea: recursion/aggregation boundary - reach `ProofGenMode` from that entrypoint and force the divergence where the set of sub-proofs aggregated and the set the output claims stop being the same set; the adjacent symbols in the same file that carry the value are `ProofData`, `ProofWithDuration`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: aggregation is complete and exact
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: drop a sub-proof and assert the aggregate fails
