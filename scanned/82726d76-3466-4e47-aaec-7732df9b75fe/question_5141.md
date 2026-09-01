# Q5141: witness commitment structure via `from_bytes` (mod.rs)

## Question
Can an unprivileged attacker who inscribes thousands of prefix-matching reveals in one Bitcoin block, controlling the block's transaction set and coinbase, drive `from_bytes` in `crates/bitcoin-da/src/helpers/mod.rs` so that the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal, breaking the invariant that segwit commitment verification is exact?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `from_bytes`
- Entrypoint: unprivileged party inscribes thousands of prefix-matching reveals in one Bitcoin block
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: witness commitment structure - reach `from_bytes` from that entrypoint and force the divergence where the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `calculate_double_sha256`, `calculate_txid`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: segwit commitment verification is exact
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: build blocks with unusual coinbase commitments and assert rejection
