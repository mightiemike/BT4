# Q3515: table key collision via `get_l2_genesis_state_root` (rpc.rs)

## Question
Can an unprivileged attacker who sends transactions that cause the same key to be written by two different modules in one block, controlling the encoded value stored under it, drive `get_l2_genesis_state_root` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the entries two logical objects occupy and the distinct keys they should occupy stop being distinct, breaking the invariant that distinct objects never share a key?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_l2_genesis_state_root`
- Entrypoint: unprivileged party sends transactions that cause the same key to be written by two different modules in one block
- Attacker controls: the encoded value stored under it
- Exploit idea: table key collision - reach `get_l2_genesis_state_root` from that entrypoint and force the divergence where the entries two logical objects occupy and the distinct keys they should occupy stop being distinct; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: distinct objects never share a key
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert colliding objects and assert both survive
