# Q5948: tx_count versus proof length via `bits_to_target` (verifier.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the proof pair it induces the node to build, drive `bits_to_target` in `crates/bitcoin-da/src/verifier.rs` so that the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal, breaking the invariant that declared counts match supplied data?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `bits_to_target`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: tx_count versus proof length - reach `bits_to_target` from that entrypoint and force the divergence where the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: declared counts match supplied data
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: mismatch them and assert `HeaderInclusionTxCountMismatch`
