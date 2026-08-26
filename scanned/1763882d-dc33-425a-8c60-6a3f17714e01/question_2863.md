# Q2863: transaction_context::MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION - access-violation handler grows an account further than intended

## Question
Can an unprivileged attacker who submits a transaction sized to sit exactly on the transaction-context protocol limits, resolving most accounts through address lookup tables to reach the account limit, drive `transaction_context::MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` to exploit the documented behaviour where the access violation handler grows an account by a full increment, so that the invariant that implicit growth performed by the fault handler is charged and bounded like explicit growth is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `transaction-context/src/lib.rs` -> `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION`
- Entrypoint: submits a transaction sized to sit exactly on the transaction-context protocol limits, resolving most accounts through address lookup tables to reach the account limit
- Attacker controls: the number of accounts, instruction count, instruction data length, account data length and per-instruction growth
- Exploit idea: Exploit the documented behaviour where the access violation handler grows an account by a full increment.
- Invariant to test: Implicit growth performed by the fault handler is charged and bounded like explicit growth.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the limit constant against a transaction built at and just past the boundary and assert the excess is rejected
