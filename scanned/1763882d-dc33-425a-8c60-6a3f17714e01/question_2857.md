# Q2857: transaction_context::IndexOfAccount - account count limit and duplicate marker collide

## Question
Can an unprivileged attacker who submits a transaction sized to sit exactly on the transaction-context protocol limits, resolving most accounts through address lookup tables to reach the account limit, drive `transaction_context::IndexOfAccount` to use an account index equal to the non-duplicate marker so a real account is read as a duplicate marker, so that the invariant that the account index space is strictly smaller than the duplicate marker value is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/lib.rs` -> `IndexOfAccount`
- Entrypoint: submits a transaction sized to sit exactly on the transaction-context protocol limits, resolving most accounts through address lookup tables to reach the account limit
- Attacker controls: the number of accounts, instruction count, instruction data length, account data length and per-instruction growth
- Exploit idea: Use an account index equal to the non-duplicate marker so a real account is read as a duplicate marker.
- Invariant to test: The account index space is strictly smaller than the duplicate marker value.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the limit constant against a transaction built at and just past the boundary and assert the excess is rejected
