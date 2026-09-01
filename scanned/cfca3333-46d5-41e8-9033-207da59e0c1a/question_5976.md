# Q5976: header chain continuity via `deserialize_reader` (block_hash.rs)

## Question
Can an unprivileged attacker who inscribes thousands of prefix-matching reveals in one Bitcoin block, controlling header fields at the boundary, drive `deserialize_reader` in `crates/bitcoin-da/src/spec/block_hash.rs` so that the previous block hash a header claims and the hash of the preceding processed block stop being equal, breaking the invariant that processed headers form one chain?

## Target
- File/function: `crates/bitcoin-da/src/spec/block_hash.rs` -> `deserialize_reader`
- Entrypoint: unprivileged party inscribes thousands of prefix-matching reveals in one Bitcoin block
- Attacker controls: header fields at the boundary
- Exploit idea: header chain continuity - reach `deserialize_reader` from that entrypoint and force the divergence where the previous block hash a header claims and the hash of the preceding processed block stop being equal; the adjacent symbols in the same file that carry the value are `BlockHashWrapper`, `as_ref`, `to_byte_array`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: processed headers form one chain
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: feed a header with a wrong parent and assert rejection
