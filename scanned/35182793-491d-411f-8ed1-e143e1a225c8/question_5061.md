# Q5061: bank::total_transaction_fee - fee collected but not accounted in the block (batching the transaction with another of)

## Question
Can an unprivileged attacker who submits transactions that are loaded, executed, committed and hashed into the bank, batching the transaction with another of its own that touches the same accounts, drive `bank::total_transaction_fee` to make filter_program_errors_and_collect_fee_details record a different total than was deducted, so that the invariant that collected fees equal the lamports removed from fee payers is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank.rs` -> `total_transaction_fee`
- Entrypoint: submits transactions that are loaded, executed, committed and hashed into the bank, batching the transaction with another of its own that touches the same accounts
- Attacker controls: transaction contents, account state it owns, submission timing across slot and epoch boundaries, and fork placement
- Exploit idea: Make filter_program_errors_and_collect_fee_details record a different total than was deducted.
- Invariant to test: Collected fees equal the lamports removed from fee payers.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank integration test replaying the crafted transaction sequence and asserting capitalization, bank hash and accounting are stable
