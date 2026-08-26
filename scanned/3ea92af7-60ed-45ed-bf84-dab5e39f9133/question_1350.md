# Q1350: transaction_account_state_info::verify_changes - uninitialized account size accounting wrong (shrinking an account to zero data)

## Question
Can an unprivileged attacker who submits a transaction whose instructions mutate accounts it lists as writable, shrinking an account to zero data while keeping a non-zero lamport balance, drive `transaction_account_state_info::verify_changes` to make get_uninitialized_accounts_size misreport so data-size deltas escape accounting, so that the invariant that accounts data size delta accounting covers newly created and resized accounts exactly is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `svm/src/transaction_account_state_info.rs` -> `verify_changes`
- Entrypoint: submits a transaction whose instructions mutate accounts it lists as writable, shrinking an account to zero data while keeping a non-zero lamport balance
- Attacker controls: which accounts are writable, their pre-execution lamports/owner/data, and what its deployed program does to them
- Exploit idea: Make get_uninitialized_accounts_size misreport so data-size deltas escape accounting.
- Invariant to test: Accounts data size delta accounting covers newly created and resized accounts exactly.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: SVM unit test running the crafted transaction and asserting verify_changes rejects the post-execution state
