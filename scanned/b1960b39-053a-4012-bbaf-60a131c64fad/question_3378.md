# Q3378: instruction_accounts::checked_add_lamports - lamport arithmetic wraps (performing the mutation from inside a)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth, drive `instruction_accounts::checked_add_lamports` to drive checked_add_lamports or checked_sub_lamports so the balance wraps instead of erroring, so that the invariant that lamport arithmetic is checked and never wraps is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `checked_add_lamports`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Drive checked_add_lamports or checked_sub_lamports so the balance wraps instead of erroring.
- Invariant to test: Lamport arithmetic is checked and never wraps.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
