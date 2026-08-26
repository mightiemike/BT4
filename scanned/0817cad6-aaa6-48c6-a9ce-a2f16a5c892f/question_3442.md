# Q3442: instruction_accounts::get_data - state serialization writes past the account length (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::get_data` to use set_state with a value larger than the account data so the write overruns, so that the invariant that serialized state always fits within the account's data length is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `get_data`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Use set_state with a value larger than the account data so the write overruns.
- Invariant to test: Serialized state always fits within the account's data length.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
