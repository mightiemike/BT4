# Q5947: sysvar_account::create_account_with_bincode - sysvar account lamports manipulated to affect rent

## Question
Can an unprivileged attacker who submits transactions that reference sysvar accounts and observe their contents, listing the sysvar account as writable in the transaction, drive `sysvar_account::create_account_with_bincode` to adjust a sysvar account's lamports so its rent handling changes capitalization, so that the invariant that sysvar account balances change only through protocol-defined adjustments is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/sysvar_account.rs` -> `create_account_with_bincode`
- Entrypoint: submits transactions that reference sysvar accounts and observe their contents, listing the sysvar account as writable in the transaction
- Attacker controls: which sysvar accounts are listed in the transaction and whether they are marked writable
- Exploit idea: Adjust a sysvar account's lamports so its rent handling changes capitalization.
- Invariant to test: Sysvar account balances change only through protocol-defined adjustments.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test comparing the sysvar account's stored bytes and fields against the bank's live values
