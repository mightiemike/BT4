# Q4951: witness commitment structure via `inner` (header.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the block's transaction set and coinbase, drive `inner` in `crates/bitcoin-da/src/spec/header.rs` so that the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal, breaking the invariant that segwit commitment verification is exact?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `inner`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: witness commitment structure - reach `inner` from that entrypoint and force the divergence where the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `prev_hash`, `verify_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: segwit commitment verification is exact
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: build blocks with unusual coinbase commitments and assert rejection
