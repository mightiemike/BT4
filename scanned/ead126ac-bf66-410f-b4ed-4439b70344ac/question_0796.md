# Q796: account_locks::lock_accounts - partial batch lock failure leaves stale locks (submitting alongside a second transaction of)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly, drive `account_locks::lock_accounts` to make try_lock_transaction_batch fail midway so already-acquired locks are never released, so that the invariant that a failed batch lock releases every lock it acquired is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `lock_accounts`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Make try_lock_transaction_batch fail midway so already-acquired locks are never released.
- Invariant to test: A failed batch lock releases every lock it acquired.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
