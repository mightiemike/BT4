# Q3437: instruction_accounts::get_owner - ownership assigned away then reclaimed within one instruction (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::get_owner` to use set_owner to hand an account to another program and mutate it before the check runs, so that the invariant that ownership checks are evaluated against the owner at the time of each mutation is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_owner`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Use set_owner to hand an account to another program and mutate it before the check runs.
- Invariant to test: Ownership checks are evaluated against the owner at the time of each mutation.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
