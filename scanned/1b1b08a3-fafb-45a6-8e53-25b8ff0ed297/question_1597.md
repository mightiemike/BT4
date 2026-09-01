# Q1597: db_commit ordering of account and storage via `commit` (db_commit.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key, controlling the CREATE2 salt and init code, drive `commit` in `crates/evm/src/evm/db_commit.rs` so that the account state committed and the storage state committed stop being from the same snapshot, breaking the invariant that commits are atomic per transaction?

## Target
- File/function: `crates/evm/src/evm/db_commit.rs` -> `commit`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key
- Attacker controls: the CREATE2 salt and init code
- Exploit idea: db_commit ordering of account and storage - reach `commit` from that entrypoint and force the divergence where the account state committed and the storage state committed stop being from the same snapshot; the adjacent symbols in the same file that carry the value are `check_account_info_changed`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: commits are atomic per transaction
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: commit under adversarial ordering and diff roots
