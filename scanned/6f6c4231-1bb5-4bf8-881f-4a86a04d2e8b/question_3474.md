# Q3474: instruction_accounts::can_data_be_changed - data written to a readonly or foreign account (passing the same account as both)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account, drive `instruction_accounts::can_data_be_changed` to call set_data_from_slice or get_data_mut on an account that can_data_be_changed should reject, so that the invariant that data mutation requires both writability and current ownership is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `can_data_be_changed`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_data_from_slice or get_data_mut on an account that can_data_be_changed should reject.
- Invariant to test: Data mutation requires both writability and current ownership.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
