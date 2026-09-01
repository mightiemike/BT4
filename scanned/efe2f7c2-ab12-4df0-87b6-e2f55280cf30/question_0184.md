# Q0184: index derived from attacker field via `get_last_pruned_l2_height` (native_db.rs)

## Question
Can an unprivileged attacker who sends transactions that cause the same key to be written by two different modules in one block, controlling the encoded value stored under it, drive `get_last_pruned_l2_height` in `crates/sovereign-sdk/full-node/db/sov-db/src/native_db.rs` so that the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot, breaking the invariant that indices are bounded and collision-free?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/native_db.rs` -> `get_last_pruned_l2_height`
- Entrypoint: unprivileged party sends transactions that cause the same key to be written by two different modules in one block
- Attacker controls: the encoded value stored under it
- Exploit idea: index derived from attacker field - reach `get_last_pruned_l2_height` from that entrypoint and force the divergence where the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot; the adjacent symbols in the same file that carry the value are `NativeDB`, `setup_schema_db`, `freeze`, `get_value_option`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: indices are bounded and collision-free
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: drive the derivation with adversarial fields and assert bounds
