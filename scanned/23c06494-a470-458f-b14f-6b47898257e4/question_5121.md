# Q5121: witness commitment structure via `target_to_work` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the number of prefix-matching reveals, drive `target_to_work` in `crates/bitcoin-da/src/verifier.rs` so that the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal, breaking the invariant that segwit commitment verification is exact?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `target_to_work`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: witness commitment structure - reach `target_to_work` from that entrypoint and force the divergence where the witness root the coinbase commits and the root recomputed from the block's wtxids stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: segwit commitment verification is exact
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: build blocks with unusual coinbase commitments and assert rejection
