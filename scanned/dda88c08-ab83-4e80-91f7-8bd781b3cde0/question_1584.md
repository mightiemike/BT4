# Q1584: rollback_accounts::new - rollback commits non-fee account changes (using the nonce account itself as)

## Question
Can an unprivileged attacker who submits transactions designed to fail after modifying state, forcing the rollback path, using the nonce account itself as the fee payer, drive `rollback_accounts::new` to get an account other than the fee payer and nonce included in the rollback write set, so that the invariant that rollback commits only the fee payer and nonce accounts is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/rollback_accounts.rs` -> `new`
- Entrypoint: submits transactions designed to fail after modifying state, forcing the rollback path, using the nonce account itself as the fee payer
- Attacker controls: which accounts are the fee payer and nonce, the failure point inside its own program, and account data sizes
- Exploit idea: Get an account other than the fee payer and nonce included in the rollback write set.
- Invariant to test: Rollback commits only the fee payer and nonce accounts.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test executing a failing transaction and asserting only fee and nonce changes are committed
