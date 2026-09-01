# Q3516: header chain continuity via `txs_commitment` (header.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the block's transaction set and coinbase, drive `txs_commitment` in `crates/bitcoin-da/src/spec/header.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `txs_commitment`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: header chain continuity - reach `txs_commitment` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `prev_hash`, `verify_hash`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
