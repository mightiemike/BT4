# Q3175: transaction_accounts::executable - executable flag set on a user account (moving lamports between two accounts the)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own, drive `transaction_accounts::executable` to set the executable flag on an account that is not a verified program, so that the invariant that the executable flag is only set by a loader after successful verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `executable`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Set the executable flag on an account that is not a verified program.
- Invariant to test: The executable flag is only set by a loader after successful verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
