# Q3521: ledger iteration order via `get_l2_blocks_range` (rpc.rs)

## Question
Can an unprivileged attacker who sends transactions that cause the same key to be written by two different modules in one block, controlling the encoded value stored under it, drive `get_l2_blocks_range` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the order an iterator yields entries and the order the schema defines stop being the same, breaking the invariant that iteration order is schema-determined?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_l2_blocks_range`
- Entrypoint: unprivileged party sends transactions that cause the same key to be written by two different modules in one block
- Attacker controls: the encoded value stored under it
- Exploit idea: ledger iteration order - reach `get_l2_blocks_range` from that entrypoint and force the divergence where the order an iterator yields entries and the order the schema defines stop being the same; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: iteration order is schema-determined
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert adversarial keys and assert stable ordering
