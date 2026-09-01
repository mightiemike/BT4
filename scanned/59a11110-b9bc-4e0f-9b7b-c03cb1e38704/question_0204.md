# Q0204: table key collision via `get_last_pruned_l2_height` (native_db.rs)

## Question
Can an unprivileged attacker who inscribes data that lands in a persisted table keyed by an attacker-influenced index, controlling the encoded value stored under it, drive `get_last_pruned_l2_height` in `crates/sovereign-sdk/full-node/db/sov-db/src/native_db.rs` so that the entries two logical objects occupy and the distinct keys they should occupy stop being distinct, breaking the invariant that distinct objects never share a key?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/native_db.rs` -> `get_last_pruned_l2_height`
- Entrypoint: unprivileged party inscribes data that lands in a persisted table keyed by an attacker-influenced index
- Attacker controls: the encoded value stored under it
- Exploit idea: table key collision - reach `get_last_pruned_l2_height` from that entrypoint and force the divergence where the entries two logical objects occupy and the distinct keys they should occupy stop being distinct; the adjacent symbols in the same file that carry the value are `NativeDB`, `setup_schema_db`, `freeze`, `get_value_option`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: distinct objects never share a key
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: insert colliding objects and assert both survive
