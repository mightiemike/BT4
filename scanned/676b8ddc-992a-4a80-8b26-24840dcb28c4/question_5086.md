# Q5086: tx_count versus proof length via `verify_header_chain_common` (verifier.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling header fields at the boundary, drive `verify_header_chain_common` in `crates/bitcoin-da/src/verifier.rs` so that the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal, breaking the invariant that declared counts match supplied data?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_header_chain_common`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: header fields at the boundary
- Exploit idea: tx_count versus proof length - reach `verify_header_chain_common` from that entrypoint and force the divergence where the `tx_count` in the header wrapper and the number of wtxids in the inclusion proof stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: declared counts match supplied data
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: mismatch them and assert `HeaderInclusionTxCountMismatch`
