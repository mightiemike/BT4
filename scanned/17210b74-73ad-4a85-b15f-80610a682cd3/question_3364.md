# Q3364: index derived from attacker field via `get_last_scanned_l1_height` (rpc.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the index or key an attacker-supplied field derives, drive `get_last_scanned_l1_height` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot, breaking the invariant that indices are bounded and collision-free?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_last_scanned_l1_height`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: index derived from attacker field - reach `get_last_scanned_l1_height` from that entrypoint and force the divergence where the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: indices are bounded and collision-free
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: drive the derivation with adversarial fields and assert bounds
