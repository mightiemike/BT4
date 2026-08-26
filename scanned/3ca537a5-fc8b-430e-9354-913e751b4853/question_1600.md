# Q1600: rollback_accounts::next - rollback restores a stale fee payer balance (resizing an account immediately before triggering)

## Question
Can an unprivileged attacker who submits transactions designed to fail after modifying state, forcing the rollback path, resizing an account immediately before triggering the failure, drive `rollback_accounts::next` to have the rollback write back a fee-payer balance that predates the fee deduction, so that the invariant that after a failed transaction the fee payer's balance is exactly pre-balance minus fee is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `svm/src/rollback_accounts.rs` -> `next`
- Entrypoint: submits transactions designed to fail after modifying state, forcing the rollback path, resizing an account immediately before triggering the failure
- Attacker controls: which accounts are the fee payer and nonce, the failure point inside its own program, and account data sizes
- Exploit idea: Have the rollback write back a fee-payer balance that predates the fee deduction.
- Invariant to test: After a failed transaction the fee payer's balance is exactly pre-balance minus fee.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: SVM unit test executing a failing transaction and asserting only fee and nonce changes are committed
