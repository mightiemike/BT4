# Q5994: network constants applied to wrong chain via `calculate_txid` (mod.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the block's transaction set and coinbase, drive `calculate_txid` in `crates/bitcoin-da/src/helpers/mod.rs` so that the constants used to validate headers and the constants of the running network stop being the same, breaking the invariant that header rules match the configured network?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `calculate_txid`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: network constants applied to wrong chain - reach `calculate_txid` from that entrypoint and force the divergence where the constants used to validate headers and the constants of the running network stop being the same; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `from_bytes`, `calculate_double_sha256`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: header rules match the configured network
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: run regtest data against mainnet constants and assert rejection
