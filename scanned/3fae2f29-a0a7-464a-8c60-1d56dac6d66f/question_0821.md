# Q821: account_locks::unlock_readonly - readonly count underflow frees a write lock (sizing the account list to sit)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, sizing the account list to sit exactly on the per-transaction lock limit, drive `account_locks::unlock_readonly` to drive unlock_readonly on an account with no outstanding readonly lock so the counter underflows, so that the invariant that readonly lock counts never go below zero is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `unlock_readonly`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, sizing the account list to sit exactly on the per-transaction lock limit
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Drive unlock_readonly on an account with no outstanding readonly lock so the counter underflows.
- Invariant to test: Readonly lock counts never go below zero.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
