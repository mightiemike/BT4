# Q815: account_locks::is_locked_readonly - readonly lock count saturates and never releases (submitting alongside a second transaction of)

## Question
Can an unprivileged attacker who submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly, drive `account_locks::is_locked_readonly` to saturate the readonly counter for an account so it can never be write-locked again, so that the invariant that readonly lock counters cannot be driven to a permanently blocking value is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `accounts-db/src/account_locks.rs` -> `is_locked_readonly`
- Entrypoint: submits transactions whose account lists are chosen to manipulate the write/read lock table, submitting alongside a second transaction of its own that locks the same account readonly
- Attacker controls: the account key list, duplicate entries, writable flags, resolved lookup addresses and batch composition
- Exploit idea: Saturate the readonly counter for an account so it can never be write-locked again.
- Invariant to test: Readonly lock counters cannot be driven to a permanently blocking value.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the lock table with the crafted batch and assert conflicting transactions cannot both hold locks
