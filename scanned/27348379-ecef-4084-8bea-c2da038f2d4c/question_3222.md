# Q3222: transaction_accounts::set_executable - executable flag set on a user account (growing the account by the maximum)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, growing the account by the maximum increment in each of several instructions, drive `transaction_accounts::set_executable` to set the executable flag on an account that is not a verified program, so that the invariant that the executable flag is only set by a loader after successful verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `set_executable`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, growing the account by the maximum increment in each of several instructions
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Set the executable flag on an account that is not a verified program.
- Invariant to test: The executable flag is only set by a loader after successful verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
