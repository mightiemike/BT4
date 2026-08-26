# Q3456: instruction_accounts::set_is_signer - signer or writable flag mutated during execution (resizing the account to zero and)

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it, drive `instruction_accounts::set_is_signer` to call set_is_signer or set_is_writable from within execution to widen privileges mid-instruction, so that the invariant that account privileges are fixed for the duration of an instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_is_signer`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, resizing the account to zero and then writing state into it
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_is_signer or set_is_writable from within execution to widen privileges mid-instruction.
- Invariant to test: Account privileges are fixed for the duration of an instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
