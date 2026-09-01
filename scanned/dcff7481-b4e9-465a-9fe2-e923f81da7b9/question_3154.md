# Q3154: index derived from attacker field via `get_rightmost_leaf` (state_db.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the encoded value stored under it, drive `get_rightmost_leaf` in `crates/sovereign-sdk/full-node/db/sov-db/src/state_db.rs` so that the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot, breaking the invariant that indices are bounded and collision-free?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/state_db.rs` -> `get_rightmost_leaf`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the encoded value stored under it
- Exploit idea: index derived from attacker field - reach `get_rightmost_leaf` from that entrypoint and force the divergence where the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot; the adjacent symbols in the same file that carry the value are `StateDB`, `setup_schema_db`, `freeze`, `next_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: indices are bounded and collision-free
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: drive the derivation with adversarial fields and assert bounds
