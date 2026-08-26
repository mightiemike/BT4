# Q3450: instruction_accounts::get_data - zeroed-account check fooled (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::get_data` to make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed, so that the invariant that is_zeroed is true only when every data byte is zero is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_data`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed.
- Invariant to test: Is_zeroed is true only when every data byte is zero.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
