# Q5480: inclusion proof over a different tree via `as_ref` (block_hash.rs)

## Question
Can an unprivileged attacker who supplies a block whose inclusion and completeness proofs disagree, controlling the block's transaction set and coinbase, drive `as_ref` in `crates/bitcoin-da/src/spec/block_hash.rs` so that the merkle root the inclusion proof reconstructs and the root in the block header stop being equal, breaking the invariant that inclusion proofs verify against the header?

## Target
- File/function: `crates/bitcoin-da/src/spec/block_hash.rs` -> `as_ref`
- Entrypoint: unprivileged party supplies a block whose inclusion and completeness proofs disagree
- Attacker controls: the block's transaction set and coinbase
- Exploit idea: inclusion proof over a different tree - reach `as_ref` from that entrypoint and force the divergence where the merkle root the inclusion proof reconstructs and the root in the block header stop being equal; the adjacent symbols in the same file that carry the value are `BlockHashWrapper`, `deserialize_reader`, `to_byte_array`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: inclusion proofs verify against the header
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: supply a proof for a sibling block and assert rejection
