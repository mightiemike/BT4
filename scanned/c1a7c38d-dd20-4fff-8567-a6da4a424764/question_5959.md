# Q5959: tx_count versus proof length via `calculate_new_difficulty` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling the number of prefix-matching reveals, drive `calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` so that the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal, breaking the invariant that declared counts match supplied data?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `calculate_new_difficulty`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: the number of prefix-matching reveals
- Exploit idea: tx_count versus proof length - reach `calculate_new_difficulty` from that entrypoint and force the divergence where the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: declared counts match supplied data
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: mismatch them and assert `HeaderInclusionTxCountMismatch`
