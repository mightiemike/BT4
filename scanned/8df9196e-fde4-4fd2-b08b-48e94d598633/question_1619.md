# Q1619: account_saver::collect_accounts_to_store - later transaction's stale copy overwrites an earlier write (making every transaction in the batch)

## Question
Can an unprivileged attacker who submits batches of transactions that succeed and fail in a chosen pattern, making every transaction in the batch a durable-nonce transaction, drive `account_saver::collect_accounts_to_store` to order two transactions in a batch so the collected set writes back a stale version of a shared account, so that the invariant that within a batch the last write to an account is the version stored is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/account_saver.rs` -> `collect_accounts_to_store`
- Entrypoint: submits batches of transactions that succeed and fail in a chosen pattern, making every transaction in the batch a durable-nonce transaction
- Attacker controls: batch composition, which transactions fail, and which accounts each transaction writes
- Exploit idea: Order two transactions in a batch so the collected set writes back a stale version of a shared account.
- Invariant to test: Within a batch the last write to an account is the version stored.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test collect_accounts_to_store on the crafted batch and assert the collected set matches the per-transaction results
