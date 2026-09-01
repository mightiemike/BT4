# Q3661: header chain continuity via `verify_header_chain` (verifier.rs)

## Question
Can an unprivileged attacker who inscribes thousands of prefix-matching reveals in one Bitcoin block, controlling header fields at the boundary, drive `verify_header_chain` in `crates/bitcoin-da/src/verifier.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_header_chain`
- Entrypoint: unprivileged party inscribes thousands of prefix-matching reveals in one Bitcoin block
- Attacker controls: header fields at the boundary
- Exploit idea: header chain continuity - reach `verify_header_chain` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
