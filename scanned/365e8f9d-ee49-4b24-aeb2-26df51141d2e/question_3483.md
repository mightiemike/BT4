# Q3483: instruction_accounts::set_state - ownership assigned away then reclaimed within one instruction (passing the same account as both)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account, drive `instruction_accounts::set_state` to use set_owner to hand an account to another program and mutate it before the check runs, so that the invariant that ownership checks are evaluated against the owner at the time of each mutation is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_state`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Use set_owner to hand an account to another program and mutate it before the check runs.
- Invariant to test: Ownership checks are evaluated against the owner at the time of each mutation.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
