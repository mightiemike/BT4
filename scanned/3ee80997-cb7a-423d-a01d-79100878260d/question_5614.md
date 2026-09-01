# Q5614: witness under-constrains a read via `compute_state_update` (zk_storage.rs)

## Question
Can an unprivileged attacker who sends a transaction that reads state written earlier in the same block by another of its transactions, controlling the size and shape of the state diff, drive `compute_state_update` in `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` so that the value the native execution read from storage and the value the guest pops from the witness stop being forced equal, breaking the invariant that every guest read is bound to the pre-state root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-state/src/zk_storage.rs` -> `compute_state_update`
- Entrypoint: unprivileged party sends a transaction that reads state written earlier in the same block by another of its transactions
- Attacker controls: the size and shape of the state diff
- Exploit idea: witness under-constrains a read - reach `compute_state_update` from that entrypoint and force the divergence where the value the native execution read from storage and the value the guest pops from the witness stop being forced equal; the adjacent symbols in the same file that carry the value are `ZkStorage`, `get`, `get_and_prove`, `get_offchain`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every guest read is bound to the pre-state root
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: replay with a mutated witness value and assert verification fails
