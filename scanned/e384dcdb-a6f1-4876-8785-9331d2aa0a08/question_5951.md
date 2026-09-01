# Q5951: network constants applied to wrong chain via `calculate_new_difficulty` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the proof pair it induces the node to build, drive `calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` so that the constants used to validate headers and the constants of the running network stop being the same, breaking the invariant that header rules match the configured network?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `calculate_new_difficulty`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: network constants applied to wrong chain - reach `calculate_new_difficulty` from that entrypoint and force the divergence where the constants used to validate headers and the constants of the running network stop being the same; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header rules match the configured network
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: run regtest data against mainnet constants and assert rejection
