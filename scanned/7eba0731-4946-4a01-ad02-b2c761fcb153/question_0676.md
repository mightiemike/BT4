# Q0676: header chain continuity via `prev_hash` (header.rs)

## Question
Can an unprivileged attacker who constructs a Bitcoin block whose coinbase witness commitment is structurally unusual, controlling header fields at the boundary, drive `prev_hash` in `crates/bitcoin-da/src/spec/header.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/spec/header.rs` -> `prev_hash`
- Entrypoint: unprivileged party constructs a Bitcoin block whose coinbase witness commitment is structurally unusual
- Attacker controls: header fields at the boundary
- Exploit idea: header chain continuity - reach `prev_hash` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `HeaderWrapper`, `BitcoinHeaderWrapper`, `verify_hash`, `txs_commitment`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
