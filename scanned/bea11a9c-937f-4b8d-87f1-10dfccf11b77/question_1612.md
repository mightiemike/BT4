# Q1612: account_saver::collect_accounts_to_store - duplicate account collected twice with different contents

## Question
Can an unprivileged attacker who submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch, drive `account_saver::collect_accounts_to_store` to get the same account key collected twice from one batch with divergent data, so that the invariant that each account key appears once in the store set with the final version is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/account_saver.rs` -> `collect_accounts_to_store`
- Entrypoint: submits batches of transactions that succeed and fail in a chosen pattern, placing a failing and a succeeding transaction that share one writable account in the same batch
- Attacker controls: batch composition, which transactions fail, and which accounts each transaction writes
- Exploit idea: Get the same account key collected twice from one batch with divergent data.
- Invariant to test: Each account key appears once in the store set with the final version.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test collect_accounts_to_store on the crafted batch and assert the collected set matches the per-transaction results
