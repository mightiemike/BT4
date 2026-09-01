# Q2846: journal output versioning via `compute_digest` (transaction.rs)

## Question
Can an unprivileged attacker who drives a stored proof output across a version boundary, controlling the version boundary crossed, drive `compute_digest` in `crates/sovereign-sdk/rollup-interface/src/state_machine/transaction.rs` so that the output version a proof commits and the version the verifier assumes stop being the same, breaking the invariant that outputs are self-describing and version-checked?

## Target
- File/function: `crates/sovereign-sdk/rollup-interface/src/state_machine/transaction.rs` -> `compute_digest`
- Entrypoint: unprivileged party drives a stored proof output across a version boundary
- Attacker controls: the version boundary crossed
- Exploit idea: journal output versioning - reach `compute_digest` from that entrypoint and force the divergence where the output version a proof commits and the version the verifier assumes stop being the same; the adjacent symbols in the same file that carry the value are `TxVersion`, `TransactionV1`, `TransactionV2`, `Transaction`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: outputs are self-describing and version-checked
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: verify a v3 output as another version and assert rejection
