# Q5586: cache pruning height handling via `commit` (prover_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling the size and shape of the state diff, drive `commit` in `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` so that the cache prune heights the circuit applies and the heights the native node applied stop being the same, breaking the invariant that pruning never changes the computed root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/prover_storage.rs` -> `commit`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: the size and shape of the state diff
- Exploit idea: cache pruning height handling - reach `commit` from that entrypoint and force the divergence where the cache prune heights the circuit applies and the heights the native node applied stop being the same; the adjacent symbols in the same file that carry the value are `ProverStorage`, `ProverStateUpdate`, `committable_latest_version`, `uncommittable_with_version`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never changes the computed root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: prune at adversarial heights and diff roots
