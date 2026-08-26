# Q1521: nonce_info::new - advance leaves the stored hash unchanged

## Question
Can an unprivileged attacker who submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution, drive `nonce_info::new` to have try_advance_nonce succeed while writing back the same durable nonce value, so that the invariant that a successful nonce advance always stores a different durable nonce is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `svm/src/nonce_info.rs` -> `new`
- Entrypoint: submits durable-nonce transactions using nonce accounts it created, resubmitting the identical nonce transaction after it failed during execution
- Attacker controls: the nonce account data, its authority, the stored blockhash, and when the transaction is resubmitted
- Exploit idea: Have try_advance_nonce succeed while writing back the same durable nonce value.
- Invariant to test: A successful nonce advance always stores a different durable nonce.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test try_advance_nonce with the crafted nonce state and assert the stored hash strictly changes
