# Q3336: instruction_accounts::get_lamports - lamport arithmetic wraps

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program, drive `instruction_accounts::get_lamports` to drive checked_add_lamports or checked_sub_lamports so the balance wraps instead of erroring, so that the invariant that lamport arithmetic is checked and never wraps is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_lamports`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Drive checked_add_lamports or checked_sub_lamports so the balance wraps instead of erroring.
- Invariant to test: Lamport arithmetic is checked and never wraps.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
