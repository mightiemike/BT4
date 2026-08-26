# Q825: account_locks::lock_write - write and read lock held simultaneously (sizing the account list to sit)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, sizing the account list to sit exactly on the per-transaction lock limit, drive `account_locks::lock_write` to obtain a write lock on an account that another in-flight transaction holds readonly, so that the invariant that no account is ever write-locked and read-locked by different transactions at the same time is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `lock_write`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, sizing the account list to sit exactly on the per-transaction lock limit
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Obtain a write lock on an account that another in-flight transaction holds readonly.
- Invariant to test: No account is ever write-locked and read-locked by different transactions at the same time.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
