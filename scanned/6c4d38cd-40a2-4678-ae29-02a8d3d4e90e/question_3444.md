# Q3444: instruction_accounts::set_data_length - rent-exempt check evaluated at the wrong length (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::set_data_length` to make is_rent_exempt_at_data_length evaluate against a length other than the final one, so that the invariant that rent exemption is evaluated against the account's final data length is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_data_length`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make is_rent_exempt_at_data_length evaluate against a length other than the final one.
- Invariant to test: Rent exemption is evaluated against the account's final data length.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
