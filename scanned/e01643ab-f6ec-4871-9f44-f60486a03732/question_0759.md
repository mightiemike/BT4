# Q759: account_locks::can_lock_accounts - partial batch lock failure leaves stale locks

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table, drive `account_locks::can_lock_accounts` to make try_lock_transaction_batch fail midway so already-acquired locks are never released, so that the invariant that a failed batch lock releases every lock it acquired is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `can_lock_accounts`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Make try_lock_transaction_batch fail midway so already-acquired locks are never released.
- Invariant to test: A failed batch lock releases every lock it acquired.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
