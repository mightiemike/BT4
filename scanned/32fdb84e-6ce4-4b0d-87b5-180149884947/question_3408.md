# Q3408: inclusion proof over a different tree via `calculate_wtxid` (mod.rs)

## Question
Can an unprivileged attacker who mines a block whose header fields sit at a consensus boundary, controlling the proof pair it induces the node to build, drive `calculate_wtxid` in `crates/bitcoin-da/src/helpers/mod.rs` so that the merkle root the inclusion proof reconstructs and the root in the block header stop being equal, breaking the invariant that inclusion proofs verify against the header?

## Target
- File/function: `crates/bitcoin-da/src/helpers/mod.rs` -> `calculate_wtxid`
- Entrypoint: unprivileged party mines a block whose header fields sit at a consensus boundary
- Attacker controls: the proof pair it induces the node to build
- Exploit idea: inclusion proof over a different tree - reach `calculate_wtxid` from that entrypoint and force the divergence where the merkle root the inclusion proof reconstructs and the root in the block header stop being equal; the adjacent symbols in the same file that carry the value are `TransactionKind`, `to_bytes`, `from_bytes`, `calculate_double_sha256`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: inclusion proofs verify against the header
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: supply a proof for a sibling block and assert rejection
