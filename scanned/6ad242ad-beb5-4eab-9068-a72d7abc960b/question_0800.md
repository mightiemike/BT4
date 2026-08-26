# Q800: account_locks::validate_account_locks - duplicate detection misses a key resolved twice (submitting alongside a second transaction of)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly, drive `account_locks::validate_account_locks` to have has_duplicates miss a pubkey that appears once statically and once via a lookup table, so that the invariant that duplicate detection runs over the fully resolved account list is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `validate_account_locks`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Have has_duplicates miss a pubkey that appears once statically and once via a lookup table.
- Invariant to test: Duplicate detection runs over the fully resolved account list.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
