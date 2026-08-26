# Q5393: transaction_execution::get_first_error - commit proceeds after a processing error (having every transaction in the batch)

## Question
Can an unprivileged attacker who submits transaction batches that are executed and committed during replay, having every transaction in the batch use the same durable nonce account, drive `transaction_execution::get_first_error` to have execute_batch commit results even though a transaction failed processing, so that the invariant that a batch that fails processing commits nothing beyond fees and nonces is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/transaction_execution.rs` -> `get_first_error`
- Entrypoint: submits transaction batches that are executed and committed during replay, having every transaction in the batch use the same durable nonce account
- Attacker controls: batch composition, per-transaction cost declarations, and which transactions fail
- Exploit idea: Have execute_batch commit results even though a transaction failed processing.
- Invariant to test: A batch that fails processing commits nothing beyond fees and nonces.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank test executing the crafted batch and asserting first-error selection and block cost checks behave identically on replay
