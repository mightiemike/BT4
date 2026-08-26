# Q754: account_locks::can_lock_accounts - account lock limit bypassed via resolved addresses

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table, drive `account_locks::can_lock_accounts` to exceed the per-transaction lock limit with addresses resolved from a lookup table after validation, so that the invariant that the lock limit counts static and resolved addresses together is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `can_lock_accounts`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Exceed the per-transaction lock limit with addresses resolved from a lookup table after validation.
- Invariant to test: The lock limit counts static and resolved addresses together.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
