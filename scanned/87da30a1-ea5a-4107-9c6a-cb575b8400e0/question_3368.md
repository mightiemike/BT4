# Q3368: instruction_accounts::is_signer - signer or writable flag mutated during execution

## Question
Can an unprivileged attacker who invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program, drive `instruction_accounts::is_signer` to call set_is_signer or set_is_writable from within execution to widen privileges mid-instruction, so that the invariant that account privileges are fixed for the duration of an instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `is_signer`
- Entrypoint: invokes its own program which reads and writes the borrowed instruction accounts, having the account be owned by the system program while the caller is its own program
- Attacker controls: which accounts are passed, their owners and sizes, and the mutations performed on them
- Exploit idea: Call set_is_signer or set_is_writable from within execution to widen privileges mid-instruction.
- Invariant to test: Account privileges are fixed for the duration of an instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the borrowed account API on the crafted account and assert ownership and privilege checks reject the mutation
