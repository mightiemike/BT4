# Q5936: sysvar_account::to_account - sysvar account written by a user transaction

## Question
Can an unprivileged attacker who submits transactions that reference sysvar accounts and observe their contents, listing the sysvar account as writable in the transaction, drive `sysvar_account::to_account` to get a sysvar account modified through a transaction rather than by the runtime, so that the invariant that sysvar accounts are never writable from user transactions is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/sysvar_account.rs` -> `to_account`
- Entrypoint: submits transactions that reference sysvar accounts and observe their contents, listing the sysvar account as writable in the transaction
- Attacker controls: which sysvar accounts are listed in the transaction and whether they are marked writable
- Exploit idea: Get a sysvar account modified through a transaction rather than by the runtime.
- Invariant to test: Sysvar accounts are never writable from user transactions.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank test comparing the sysvar account's stored bytes and fields against the bank's live values
