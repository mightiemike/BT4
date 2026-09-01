# Q4153: witness under-constrains a read via `as_ref` (mod.rs)

## Question
Can an unprivileged attacker who sends a transaction that writes, deletes and rewrites the same storage key in one block, controlling intra-block ordering of its own transactions, drive `as_ref` in `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` so that the value the native execution read from storage and the value the guest pops from the witness stop being forced equal, breaking the invariant that every guest read is bound to the pre-state root?

## Target
- File/function: `crates/sovereign-sdk/module-system/sov-modules-core/src/storage/mod.rs` -> `as_ref`
- Entrypoint: unprivileged party sends a transaction that writes, deletes and rewrites the same storage key in one block
- Attacker controls: intra-block ordering of its own transactions
- Exploit idea: witness under-constrains a read - reach `as_ref` from that entrypoint and force the divergence where the value the native execution read from storage and the value the guest pops from the witness stop being forced equal; the adjacent symbols in the same file that carry the value are `StorageKey`, `StorageValue`, `StorageProof`, `Storage`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: every guest read is bound to the pre-state root
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: replay with a mutated witness value and assert verification fails
