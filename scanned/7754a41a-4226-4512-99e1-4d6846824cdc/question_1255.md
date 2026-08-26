# Q1255: account_loader::update_accounts_for_successful_tx - failed transaction commits account changes

## Question
Can an unprivileged attacker who submits a transaction whose account list, fee payer and program accounts it fully chooses, using an account it owns as both the fee payer and a writable instruction account, drive `account_loader::update_accounts_for_successful_tx` to have update_accounts_for_failed_tx persist changes other than the fee and nonce advance, so that the invariant that a failed transaction commits only the fee deduction and nonce advance is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/account_loader.rs` -> `update_accounts_for_successful_tx`
- Entrypoint: submits a transaction whose account list, fee payer and program accounts it fully chooses, using an account it owns as both the fee payer and a writable instruction account
- Attacker controls: every account key, its owner, data size and lamports, the fee payer, and the declared loaded-accounts data size limit
- Exploit idea: Have update_accounts_for_failed_tx persist changes other than the fee and nonce advance.
- Invariant to test: A failed transaction commits only the fee deduction and nonce advance.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test loading the crafted transaction and asserting the loaded account set, sizes and fee-payer validation match expectations
