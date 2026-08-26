# Q498: check_transactions::load_message_nonce_data - nonce data parsed from a non-nonce account (pairing the transaction with a durable)

## Question
Can an unprivileged attacker who submits a transaction with an attacker-chosen recent blockhash or durable nonce account, pairing the transaction with a durable nonce account the attacker created but does not authorize, drive `check_transactions::load_message_nonce_data` to make load_message_nonce_data accept an attacker-owned account that is not a system nonce account, so that the invariant that nonce state is only read from a system-program-owned, correctly initialised nonce account is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank/check_transactions.rs` -> `load_message_nonce_data`
- Entrypoint: submits a transaction with an attacker-chosen recent blockhash or durable nonce account, pairing the transaction with a durable nonce account the attacker created but does not authorize
- Attacker controls: the recent_blockhash field, the nonce account and its authority, instruction ordering, and resubmission timing
- Exploit idea: Make load_message_nonce_data accept an attacker-owned account that is not a system nonce account.
- Invariant to test: Nonce state is only read from a system-program-owned, correctly initialised nonce account.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test: process the transaction twice across the crafted slot boundary and assert the second attempt is rejected
