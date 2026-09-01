# Q5888: header chain continuity via `verify_header_chain_mainnet` (verifier.rs)

## Question
Can an unprivileged attacker who inscribes thousands of prefix-matching reveals in one Bitcoin block, controlling the block's transaction set and coinbase, drive `verify_header_chain_mainnet` in `crates/bitcoin-da/src/verifier.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/verifier.rs` -> `verify_header_chain_mainnet`
- Entrypoint: unprivileged party inscribes thousands of prefix-matching reveals in one Bitcoin block
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: header chain continuity - reach `verify_header_chain_mainnet` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinVerifier`, `ValidationError`, `decompress_chunks`, `verify_transactions`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
