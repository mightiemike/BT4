# Q3427: table key collision via `get_head_l2_block_height` (rpc.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the index or key an attacker-supplied field derives, drive `get_head_l2_block_height` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the entries two logical objects occupy and the distinct keys they should occupy stop being distinct, breaking the invariant that distinct objects never share a key?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_head_l2_block_height`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the index or key an attacker-supplied field derives
- Exploit idea: table key collision - reach `get_head_l2_block_height` from that entrypoint and force the divergence where the entries two logical objects occupy and the distinct keys they should occupy stop being distinct; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: distinct objects never share a key
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert colliding objects and assert both survive
