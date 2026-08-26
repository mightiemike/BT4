# Q3049: transaction_accounts::try_borrow_mut - simultaneous mutable and shared borrows of one account

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, passing the same account twice so both copies are borrowed in one instruction, drive `transaction_accounts::try_borrow_mut` to obtain try_borrow_mut while another frame holds try_borrow on the same account, so that the invariant that an account is never mutably borrowed while any other borrow is live is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `try_borrow_mut`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, passing the same account twice so both copies are borrowed in one instruction
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Obtain try_borrow_mut while another frame holds try_borrow on the same account.
- Invariant to test: An account is never mutably borrowed while any other borrow is live.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
