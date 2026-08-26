# Q1364: transaction_account_state_info::new_pre_exec - pre and post snapshots taken over different account sets (reassigning ownership of an account in)

## Question
Can an unprivileged attacker who submits a transaction whose instructions mutate accounts it lists as writable, reassigning ownership of an account in the final instruction of the transaction, drive `transaction_account_state_info::new_pre_exec` to make new_pre_exec and new_post_exec disagree on which accounts are writable so a change is never compared, so that the invariant that the pre and post state sets cover exactly the same accounts in the same order is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/transaction_account_state_info.rs` -> `new_pre_exec`
- Entrypoint: submits a transaction whose instructions mutate accounts it lists as writable, reassigning ownership of an account in the final instruction of the transaction
- Attacker controls: which accounts are writable, their pre-execution lamports/owner/data, and what its deployed program does to them
- Exploit idea: Make new_pre_exec and new_post_exec disagree on which accounts are writable so a change is never compared.
- Invariant to test: The pre and post state sets cover exactly the same accounts in the same order.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test running the crafted transaction and asserting verify_changes rejects the post-execution state
