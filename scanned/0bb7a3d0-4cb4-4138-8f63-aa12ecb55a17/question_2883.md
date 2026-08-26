# Q2883: transaction_context::MAX_ACCOUNTS_PER_INSTRUCTION - instruction data length limit not enforced at every entry (growing one account by the maximum)

## Question
Can an unprivileged attacker who submits a transaction sized to sit exactly on the transaction-context protocol limits, growing one account by the maximum increment in every instruction of the transaction, drive `transaction_context::MAX_ACCOUNTS_PER_INSTRUCTION` to pass instruction data longer than MAX_INSTRUCTION_DATA_LEN through a path that does not check it, so that the invariant that every instruction, top level or CPI, respects the instruction data length limit is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `transaction-context/src/lib.rs` -> `MAX_ACCOUNTS_PER_INSTRUCTION`
- Entrypoint: submits a transaction sized to sit exactly on the transaction-context protocol limits, growing one account by the maximum increment in every instruction of the transaction
- Attacker controls: the number of accounts, instruction count, instruction data length, account data length and per-instruction growth
- Exploit idea: Pass instruction data longer than MAX_INSTRUCTION_DATA_LEN through a path that does not check it.
- Invariant to test: Every instruction, top level or CPI, respects the instruction data length limit.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the limit constant against a transaction built at and just past the boundary and assert the excess is rejected
