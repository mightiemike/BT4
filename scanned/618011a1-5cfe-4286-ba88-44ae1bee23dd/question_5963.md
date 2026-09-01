# Q5963: header chain continuity via `calculate_wtxid` (mod.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the block's transaction set and coinbase, drive `calculate_wtxid` in `crates/bitcoin-da/src/helpers/mod.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `calculate_wtxid`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: header chain continuity - reach `calculate_wtxid` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `from_bytes`, `calculate_double_sha256`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
