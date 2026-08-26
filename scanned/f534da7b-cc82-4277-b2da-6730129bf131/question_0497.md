# Q497: check_transactions::load_message_nonce_data - nonce account authority not enforced (pairing the transaction with a durable)

## Question
Can an unprivileged attacker who submits a transaction with an attacker-chosen recent blockhash or durable nonce account, pairing the transaction with a durable nonce account the attacker created but does not authorize, drive `check_transactions::load_message_nonce_data` to advance or consume a nonce account whose authority the attacker does not hold, so that the invariant that only the nonce authority's signature can consume a durable nonce is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank/check_transactions.rs` -> `load_message_nonce_data`
- Entrypoint: submits a transaction with an attacker-chosen recent blockhash or durable nonce account, pairing the transaction with a durable nonce account the attacker created but does not authorize
- Attacker controls: the recent_blockhash field, the nonce account and its authority, instruction ordering, and resubmission timing
- Exploit idea: Advance or consume a nonce account whose authority the attacker does not hold.
- Invariant to test: Only the nonce authority's signature can consume a durable nonce.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test: process the transaction twice across the crafted slot boundary and assert the second attempt is rejected
