# Q1250: account_loader::load_transaction_account - program account loaded from the wrong owner chain

## Question
Can an unprivileged attacker who submits a transaction whose account list, fee payer and program accounts it fully chooses, using an account it owns as both the fee payer and a writable instruction account, drive `account_loader::load_transaction_account` to get an account loaded as an executable program whose owner is not a recognised loader, so that the invariant that only accounts owned by a valid loader are loaded as executable programs is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/account_loader.rs` -> `load_transaction_account`
- Entrypoint: submits a transaction whose account list, fee payer and program accounts it fully chooses, using an account it owns as both the fee payer and a writable instruction account
- Attacker controls: every account key, its owner, data size and lamports, the fee payer, and the declared loaded-accounts data size limit
- Exploit idea: Get an account loaded as an executable program whose owner is not a recognised loader.
- Invariant to test: Only accounts owned by a valid loader are loaded as executable programs.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test loading the crafted transaction and asserting the loaded account set, sizes and fee-payer validation match expectations
