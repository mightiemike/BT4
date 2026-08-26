# Q3448: instruction_accounts::is_owned_by_current_program - executable flag flipped by a non-loader (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::is_owned_by_current_program` to call set_executable from a program that is not the account's loader, so that the invariant that only a loader may set the executable flag is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `is_owned_by_current_program`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_executable from a program that is not the account's loader.
- Invariant to test: Only a loader may set the executable flag.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
