# Q1606: account_saver::collect_accounts_to_store - successful transaction's write dropped

## Question
Can an unprivileged attacker who submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch, drive `account_saver::collect_accounts_to_store` to make collect_accounts_for_successful_tx omit a modified account so committed state loses a write, so that the invariant that every account modified by a successful transaction reaches the store set is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/account_saver.rs` -> `collect_accounts_to_store`
- Entrypoint: submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch
- Attacker controls: batch composition, which transactions fail, and which accounts each transaction writes
- Exploit idea: Make collect_accounts_for_successful_tx omit a modified account so committed state loses a write.
- Invariant to test: Every account modified by a successful transaction reaches the store set.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test collect_accounts_to_store on the crafted batch and assert the collected set matches the per-transaction results
