# Q1628: account_saver::max_number_of_accounts_to_collect - failed transaction's writes collected for storage (having the shared account resized by)

## Question
Can an unprivileged attacker who submits batches of transactions that succeed and fail in a chosen pattern, having the shared account resized by the first transaction and read by the second, drive `account_saver::max_number_of_accounts_to_collect` to make collect_accounts_for_failed_tx include accounts the failed transaction modified, so that the invariant that a failed transaction contributes only its fee payer and nonce to the store set is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/account_saver.rs` -> `max_number_of_accounts_to_collect`
- Entrypoint: submits batches of transactions that succeed and fail in a chosen pattern, having the shared account resized by the first transaction and read by the second
- Attacker controls: batch composition, which transactions fail, and which accounts each transaction writes
- Exploit idea: Make collect_accounts_for_failed_tx include accounts the failed transaction modified.
- Invariant to test: A failed transaction contributes only its fee payer and nonce to the store set.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test collect_accounts_to_store on the crafted batch and assert the collected set matches the per-transaction results
