# Q771: account_locks::validate_account_locks - reserved or sysvar account write-locked

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table, drive `account_locks::validate_account_locks` to acquire a write lock on a reserved account the runtime assumes is never writable, so that the invariant that reserved account keys can never be write-locked by a user transaction is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `validate_account_locks`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, listing the same account twice, once statically and once via an address lookup table
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Acquire a write lock on a reserved account the runtime assumes is never writable.
- Invariant to test: Reserved account keys can never be write-locked by a user transaction.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
