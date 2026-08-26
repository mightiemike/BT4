# Q3138: transaction_accounts::rent_epoch - rent epoch or metadata rewritten by a program (resizing the account inside a CPI)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, resizing the account inside a CPI callee and reading it in the caller, drive `transaction_accounts::rent_epoch` to set rent_epoch or other protocol metadata from inside a program, so that the invariant that protocol-managed account metadata is not writable by programs is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `rent_epoch`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, resizing the account inside a CPI callee and reading it in the caller
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Set rent_epoch or other protocol metadata from inside a program.
- Invariant to test: Protocol-managed account metadata is not writable by programs.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
