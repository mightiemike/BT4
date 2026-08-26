# Q3490: instruction_accounts::get_rent_epoch - rent-exempt check evaluated at the wrong length (passing the same account as both)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account, drive `instruction_accounts::get_rent_epoch` to make is_rent_exempt_at_data_length evaluate against a length other than the final one, so that the invariant that rent exemption is evaluated against the account's final data length is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_rent_epoch`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, passing the same account as both a readonly and a writable instruction account
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make is_rent_exempt_at_data_length evaluate against a length other than the final one.
- Invariant to test: Rent exemption is evaluated against the account's final data length.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
