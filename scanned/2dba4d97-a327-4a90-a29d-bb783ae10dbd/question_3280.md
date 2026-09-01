# Q3280: index derived from attacker field via `get_l2_block_by_hash` (rpc.rs)

## Question
Can an unprivileged attacker who sends transactions that cause the same key to be written by two different modules in one block, controlling the index or key an attacker-supplied field derives, drive `get_l2_block_by_hash` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot, breaking the invariant that indices are bounded and collision-free?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_l2_block_by_hash`
- Entrypoint: unprivileged party sends transactions that cause the same key to be written by two different modules in one block
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: index derived from attacker field - reach `get_l2_block_by_hash` from that entrypoint and force the divergence where the storage index an attacker-influenced field derives and the index the protocol intends stop being the same slot; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_number`, `get_l2_blocks_range`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: indices are bounded and collision-free
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: drive the derivation with adversarial fields and assert bounds
