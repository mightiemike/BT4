# Q3372: instruction_accounts::new - index-in-transaction resolves to another account

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program, drive `instruction_accounts::new` to make get_index_in_transaction or get_key return a different account than the borrow refers to, so that the invariant that a borrowed account's key and index always identify the same account is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `new`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make get_index_in_transaction or get_key return a different account than the borrow refers to.
- Invariant to test: A borrowed account's key and index always identify the same account.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
