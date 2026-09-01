# Q1646: witness commitment structure via `calculate_new_difficulty` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the number of prefix-matching reveals, drive `calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` so that the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal, breaking the invariant that segwit commitment verification is exact?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `calculate_new_difficulty`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: witness commitment structure - reach `calculate_new_difficulty` from that entrypoint and force the divergence where the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: segwit commitment verification is exact
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: build blocks with unusual coinbase commitments and assert rejection
