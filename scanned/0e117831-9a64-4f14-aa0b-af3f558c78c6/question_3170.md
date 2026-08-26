# Q3170: transaction_accounts::set_owner - owner changed without ownership authority (moving lamports between two accounts the)

## Question
Can an unprivileged attacker who invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own, drive `transaction_accounts::set_owner` to call set_owner or copy_into_owner_from_slice on an account the executing program does not own, so that the invariant that only the current owner program may change an account's owner is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/transaction_accounts.rs` -> `set_owner`
- Entrypoint: invokes its own program which borrows, resizes and mutates the accounts it was passed, moving lamports between two accounts the program does not own
- Attacker controls: borrow patterns, resize amounts, lamport moves, owner assignment and duplicate account usage
- Exploit idea: Call set_owner or copy_into_owner_from_slice on an account the executing program does not own.
- Invariant to test: Only the current owner program may change an account's owner.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrow/resize/lamport sequence and assert borrow rules, resize limits and lamport deltas hold
