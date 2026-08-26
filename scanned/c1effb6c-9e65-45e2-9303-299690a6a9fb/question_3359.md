# Q3359: instruction_accounts::is_zeroed - zeroed-account check fooled

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program, drive `instruction_accounts::is_zeroed` to make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed, so that the invariant that is_zeroed is true only when every data byte is zero is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `is_zeroed`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Make is_zeroed report a non-empty account as zeroed so a reinitialization guard is bypassed.
- Invariant to test: Is_zeroed is true only when every data byte is zero.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
