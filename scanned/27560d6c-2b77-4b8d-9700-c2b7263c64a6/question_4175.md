# Q4175: system_processor::is_signer - transfer from an account the sender does not control (targeting an account that another instruction)

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed, drive `system_processor::is_signer` to move lamports out of an account whose signature the transaction lacks, so that the invariant that a transfer debits only accounts that signed or are owned by the invoking program is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `is_signer`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Move lamports out of an account whose signature the transaction lacks.
- Invariant to test: A transfer debits only accounts that signed or are owned by the invoking program.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
