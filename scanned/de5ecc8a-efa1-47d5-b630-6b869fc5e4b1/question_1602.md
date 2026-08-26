# Q1602: account_saver::collect_accounts_for_failed_tx - failed transaction's writes collected for storage

## Question
Can an unprivileged attacker who submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch, drive `account_saver::collect_accounts_for_failed_tx` to make collect_accounts_for_failed_tx include accounts the failed transaction modified, so that the invariant that a failed transaction contributes only its fee payer and nonce to the store set is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/account_saver.rs` -> `collect_accounts_for_failed_tx`
- Entrypoint: submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch
- Attacker controls: batch composition, which transactions fail, and which accounts each transaction writes
- Exploit idea: Make collect_accounts_for_failed_tx include accounts the failed transaction modified.
- Invariant to test: A failed transaction contributes only its fee payer and nonce to the store set.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test collect_accounts_to_store on the crafted batch and assert the collected set matches the per-transaction results
