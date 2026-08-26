# Q1596: rollback_accounts::new - empty rollback set on a charged transaction (using the nonce account itself as)

## Question
Can an unprivileged attacker who submits transactions designed to fail after modifying state, forcing the rollback path, using the nonce account itself as the fee payer, drive `rollback_accounts::new` to produce a failed transaction that is charged a fee but whose rollback set is empty, so that the invariant that any charged transaction commits at least the fee-payer debit is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `svm/src/rollback_accounts.rs` -> `new`
- Entrypoint: submits transactions designed to fail after modifying state, forcing the rollback path, using the nonce account itself as the fee payer
- Attacker controls: which accounts are the fee payer and nonce, the failure point inside its own program, and account data sizes
- Exploit idea: Produce a failed transaction that is charged a fee but whose rollback set is empty.
- Invariant to test: Any charged transaction commits at least the fee-payer debit.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: SVM unit test executing a failing transaction and asserting only fee and nonce changes are committed
