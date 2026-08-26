# Q2888: transaction_context::MAX_ACCOUNTS_PER_INSTRUCTION - account count limit and duplicate marker collide (nesting CPI so the instruction trace)

## Question
Can an unprivileged attacker who submits a transaction sized to sit exactly on the transaction-context protocol limits, nesting CPI so the instruction trace fills entirely with inner instructions, drive `transaction_context::MAX_ACCOUNTS_PER_INSTRUCTION` to use an account index equal to the non-duplicate marker so a real account is read as a duplicate marker, so that the invariant that the account index space is strictly smaller than the duplicate marker value is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/lib.rs` -> `MAX_ACCOUNTS_PER_INSTRUCTION`
- Entrypoint: submits a transaction sized to sit exactly on the transaction-context protocol limits, nesting CPI so the instruction trace fills entirely with inner instructions
- Attacker controls: the number of accounts, instruction count, instruction data length, account data length and per-instruction growth
- Exploit idea: Use an account index equal to the non-duplicate marker so a real account is read as a duplicate marker.
- Invariant to test: The account index space is strictly smaller than the duplicate marker value.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the limit constant against a transaction built at and just past the boundary and assert the excess is rejected
