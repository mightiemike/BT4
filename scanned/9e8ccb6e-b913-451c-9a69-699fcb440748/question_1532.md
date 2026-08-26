# Q1532: nonce_info::try_advance_nonce - advance not applied when execution fails

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution, drive `nonce_info::try_advance_nonce` to make a failing nonce transaction skip the advance so the same nonce transaction can be retried indefinitely, so that the invariant that a nonce transaction that is charged a fee always advances its nonce is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `try_advance_nonce`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Make a failing nonce transaction skip the advance so the same nonce transaction can be retried indefinitely.
- Invariant to test: A nonce transaction that is charged a fee always advances its nonce.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
