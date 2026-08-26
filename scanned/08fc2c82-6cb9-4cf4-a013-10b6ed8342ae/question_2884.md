# Q2884: transaction_context::IndexOfAccount - IndexOfAccount truncation aliases two accounts (growing one account by the maximum)

## Question
Can an unprivileged attacker who submits a transaction sized to sit exactly on the transaction-context protocol limits, growing one account by the maximum increment in every instruction of the transaction, drive `transaction_context::IndexOfAccount` to exploit the u16 index type so two different transaction accounts resolve to one index, so that the invariant that every account index uniquely identifies one transaction account is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/lib.rs` -> `IndexOfAccount`
- Entrypoint: submits a transaction sized to sit exactly on the transaction-context protocol limits, growing one account by the maximum increment in every instruction of the transaction
- Attacker controls: the number of accounts, instruction count, instruction data length, account data length and per-instruction growth
- Exploit idea: Exploit the u16 index type so two different transaction accounts resolve to one index.
- Invariant to test: Every account index uniquely identifies one transaction account.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the limit constant against a transaction built at and just past the boundary and assert the excess is rejected
