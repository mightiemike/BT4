# Q1899: cache pruning height handling via `get` (mod.rs)

## Question
Can an unprivileged attacker who sends an L2 transaction whose execution touches a JMT slot no other transaction touches, controlling which JMT keys are read and written, drive `get` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` so that the cache prune heights the circuit applies and the heights the native node applied stop being the same, breaking the invariant that pruning never changes the computed root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` -> `get`
- Entrypoint: unprivileged party sends an L2 transaction whose execution touches a JMT slot no other transaction touches
- Attacker controls: which JMT keys are read and written
- Exploit idea: cache pruning height handling - reach `get` from that entrypoint and force the divergence where the cache prune heights the circuit applies and the heights the native node applied stop being the same; the adjacent symbols in the same file that carry the value are `StorageKey`, `StorageValue`, `StorageProof`, `Storage`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never changes the computed root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: prune at adversarial heights and diff roots
