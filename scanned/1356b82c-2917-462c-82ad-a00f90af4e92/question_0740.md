# Q740: account_locks::lock_accounts - duplicate keys unlock an account still held

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table, drive `account_locks::lock_accounts` to list the same account twice so unlock_accounts releases a lock another transaction in the batch still holds, so that the invariant that lock and unlock counts are exactly balanced per account per transaction is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `lock_accounts`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: List the same account twice so unlock_accounts releases a lock another transaction in the batch still holds.
- Invariant to test: Lock and unlock counts are exactly balanced per account per transaction.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
