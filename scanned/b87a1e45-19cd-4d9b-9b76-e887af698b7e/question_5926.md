# Q5926: network constants applied to wrong chain via `target_to_work` (verifier.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling header fields at the boundary, drive `target_to_work` in `crates/bitcoin-da/src/verifier.rs` so that the constants used to validate headers and the constants of the running network stop being the same, breaking the invariant that header rules match the configured network?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `target_to_work`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: header fields at the boundary
- Exploit idea: network constants applied to wrong chain - reach `target_to_work` from that entrypoint and force the divergence where the constants used to validate headers and the constants of the running network stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header rules match the configured network
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: run regtest data against mainnet constants and assert rejection
