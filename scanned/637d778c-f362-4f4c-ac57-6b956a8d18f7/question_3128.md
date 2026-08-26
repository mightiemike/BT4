# Q3128: transaction_accounts::set_owner - executable flag set on a user account (resizing the account inside a CPI)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, resizing the account inside a CPI callee and reading it in the caller, drive `transaction_accounts::set_owner` to set the executable flag on an account that is not a verified program, so that the invariant that the executable flag is only set by a loader after successful verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `set_owner`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, resizing the account inside a CPI callee and reading it in the caller
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Set the executable flag on an account that is not a verified program.
- Invariant to test: The executable flag is only set by a loader after successful verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
