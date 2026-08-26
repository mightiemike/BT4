# Q3428: instruction_accounts::get_data_mut - data written to a readonly or foreign account (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::get_data_mut` to call set_data_from_slice or get_data_mut on an account that can_data_be_changed should reject, so that the invariant that data mutation requires both writability and current ownership is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_data_mut`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_data_from_slice or get_data_mut on an account that can_data_be_changed should reject.
- Invariant to test: Data mutation requires both writability and current ownership.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
