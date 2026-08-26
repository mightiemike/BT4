# Q3375: instruction_accounts::checked_sub_lamports - lamports moved out of an account the program does not own (performing the mutation from inside a)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth, drive `instruction_accounts::checked_sub_lamports` to call set_lamports or checked_sub_lamports on an account owned by another program, so that the invariant that only the owning program may reduce an account's lamports is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `checked_sub_lamports`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_lamports or checked_sub_lamports on an account owned by another program.
- Invariant to test: Only the owning program may reduce an account's lamports.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
