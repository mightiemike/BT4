# Q3207: cache pruning height handling via `with_witness_and_log` (scratchpad.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling the size and shape of the state diff, drive `with_witness_and_log` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` so that the cache prune heights the circuit applies and the heights the native node applied stop being the same, breaking the invariant that pruning never changes the computed root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/scratchpad.rs` -> `with_witness_and_log`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: the size and shape of the state diff
- Exploit idea: cache pruning height handling - reach `with_witness_and_log` from that entrypoint and force the divergence where the cache prune heights the circuit applies and the heights the native node applied stop being the same; the adjacent symbols in the same file that carry the value are `StateReaderAndWriter`, `StateDelta`, `AccessoryDelta`, `OffchainDelta`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: pruning never changes the computed root
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: prune at adversarial heights and diff roots
