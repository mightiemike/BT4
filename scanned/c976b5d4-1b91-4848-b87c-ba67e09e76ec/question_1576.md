# Q1576: network constants applied to wrong chain via `verify_target_hash` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the number of prefix-matching reveals, drive `verify_target_hash` in `crates/bitcoin-da/src/verifier.rs` so that the constants used to validate headers and the constants of the running network stop being the same, breaking the invariant that header rules match the configured network?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_target_hash`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: network constants applied to wrong chain - reach `verify_target_hash` from that entrypoint and force the divergence where the constants used to validate headers and the constants of the running network stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header rules match the configured network
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: run regtest data against mainnet constants and assert rejection
