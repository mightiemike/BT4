# Q5995: block hash versus header fields via `calculate_txid` (mod.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the block's transaction set and coinbase, drive `calculate_txid` in `crates/bitcoin-da/src/helpers/mod.rs` so that the block hash used as a key and the hash recomputed from the header stop being equal, breaking the invariant that block identity is derived, never taken on trust?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `calculate_txid`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: block hash versus header fields - reach `calculate_txid` from that entrypoint and force the divergence where the block hash used as a key and the hash recomputed from the header stop being equal; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `from_bytes`, `calculate_double_sha256`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: block identity is derived, never taken on trust
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: supply a header whose stored hash disagrees and assert rejection
