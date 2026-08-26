# Q1363: transaction_account_state_info::iter_writable_accounts - post-execution verification skips an account (reassigning ownership of an account in)

## Question
Can an unprivileged attacker who submits a transaction whose instructions mutate accounts it lists as writable, reassigning ownership of an account in the final instruction of the transaction, drive `transaction_account_state_info::iter_writable_accounts` to have verify_changes ignore an account whose lamports, owner or data changed illegally, so that the invariant that every writable account is re-verified after execution is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/transaction_account_state_info.rs` -> `iter_writable_accounts`
- Entrypoint: submits a transaction whose instructions mutate accounts it lists as writable, reassigning ownership of an account in the final instruction of the transaction
- Attacker controls: which accounts are writable, their pre-execution lamports/owner/data, and what its deployed program does to them
- Exploit idea: Have verify_changes ignore an account whose lamports, owner or data changed illegally.
- Invariant to test: Every writable account is re-verified after execution.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test running the crafted transaction and asserting verify_changes rejects the post-execution state
