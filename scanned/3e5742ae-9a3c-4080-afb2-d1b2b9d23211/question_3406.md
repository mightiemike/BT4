# Q3406: instruction_accounts::set_data_from_slice - zeroed-account check fooled (performing the mutation from inside a)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth, drive `instruction_accounts::set_data_from_slice` to make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed, so that the invariant that is_zeroed is true only when every data byte is zero is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_data_from_slice`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, performing the mutation from inside a CPI callee at maximum depth
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed.
- Invariant to test: Is_zeroed is true only when every data byte is zero.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
