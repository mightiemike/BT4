# Q1569: rollback_accounts::new - fee payer and nonce are the same account and one write is lost

## Question
Can an unprivileged attacker who submits transactions designed to fail after modifying state, forcing the rollback path, failing the transaction inside a CPI several levels deep, drive `rollback_accounts::new` to use one account as both fee payer and nonce so the rollback writes conflict, so that the invariant that when fee payer and nonce coincide, the committed state reflects both the fee deduction and the nonce advance is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `svm/src/rollback_accounts.rs` -> `new`
- Entrypoint: submits transactions designed to fail after modifying state, forcing the rollback path, failing the transaction inside a CPI several levels deep
- Attacker controls: which accounts are the fee payer and nonce, the failure point inside its own program, and account data sizes
- Exploit idea: Use one account as both fee payer and nonce so the rollback writes conflict.
- Invariant to test: When fee payer and nonce coincide, the committed state reflects both the fee deduction and the nonce advance.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: SVM unit test executing a failing transaction and asserting only fee and nonce changes are committed
