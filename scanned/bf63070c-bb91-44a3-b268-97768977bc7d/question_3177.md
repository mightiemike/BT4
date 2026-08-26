# Q3177: transaction_accounts::is_shared - copy-on-write shared account mutated in place (moving lamports between two accounts the)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own, drive `transaction_accounts::is_shared` to mutate a shared (copy-on-write) account without triggering the copy so another holder sees the change, so that the invariant that a shared account is copied before any mutation is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `is_shared`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Mutate a shared (copy-on-write) account without triggering the copy so another holder sees the change.
- Invariant to test: A shared account is copied before any mutation.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
