# Q3492: witness commitment structure via `prev_hash` (header.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling header fields at the boundary, drive `prev_hash` in `crates/bitcoin-da/src/spec/header.rs` so that the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal, breaking the invariant that segwit commitment verification is exact?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `prev_hash`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: header fields at the boundary
- Exploit idea: witness commitment structure - reach `prev_hash` from that entrypoint and force the divergence where the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `verify_hash`, `txs_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: segwit commitment verification is exact
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: build blocks with unusual coinbase commitments and assert rejection
