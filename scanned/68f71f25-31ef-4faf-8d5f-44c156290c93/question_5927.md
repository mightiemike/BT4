# Q5927: witness under-constrains a read via `get_and_prove` (zk_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling which JMT keys are read and written, drive `get_and_prove` in `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` so that the value the native execution read from storage and the value the guest pops from the witness stop being forced equal, breaking the invariant that every guest read is bound to the pre-state root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` -> `get_and_prove`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: which JMT keys are read and written
- Exploit idea: witness under-constrains a read - reach `get_and_prove` from that entrypoint and force the divergence where the value the native execution read from storage and the value the guest pops from the witness stop being forced equal; the adjacent symbols in the same file that carry the value are `ZkStorage`, `get`, `get_offchain`, `compute_state_update`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every guest read is bound to the pre-state root
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: replay with a mutated witness value and assert verification fails
