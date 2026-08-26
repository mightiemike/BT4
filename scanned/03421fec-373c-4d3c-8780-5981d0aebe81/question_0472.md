# Q472: check_transactions::filter_v1_transactions - v1 transaction filter admits a transaction it should drop

## Question
Can an unprivileged attacker who submits a transaction with an attacker-chosen recent blockhash or durable nonce account, resubmitting the identical signed transaction in the next slot after it already succeeded, drive `check_transactions::filter_v1_transactions` to pass filter_v1_transactions with a transaction whose feature-gated form should be rejected at this slot, so that the invariant that the admitted transaction set is a pure function of the slot's activated features is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/check_transactions.rs` -> `filter_v1_transactions`
- Entrypoint: submits a transaction with an attacker-chosen recent blockhash or durable nonce account, resubmitting the identical signed transaction in the next slot after it already succeeded
- Attacker controls: the recent_blockhash field, the nonce account and its authority, instruction ordering, and resubmission timing
- Exploit idea: Pass filter_v1_transactions with a transaction whose feature-gated form should be rejected at this slot.
- Invariant to test: The admitted transaction set is a pure function of the slot's activated features.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank integration test: process the transaction twice across the crafted slot boundary and assert the second attempt is rejected
