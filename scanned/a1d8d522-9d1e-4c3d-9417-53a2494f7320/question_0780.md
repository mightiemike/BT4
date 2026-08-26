# Q780: account_locks::has_duplicates - duplicate keys unlock an account still held (submitting alongside a second transaction of)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly, drive `account_locks::has_duplicates` to list the same account twice so unlock_accounts releases a lock another transaction in the batch still holds, so that the invariant that lock and unlock counts are exactly balanced per account per transaction is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `has_duplicates`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: List the same account twice so unlock_accounts releases a lock another transaction in the batch still holds.
- Invariant to test: Lock and unlock counts are exactly balanced per account per transaction.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
