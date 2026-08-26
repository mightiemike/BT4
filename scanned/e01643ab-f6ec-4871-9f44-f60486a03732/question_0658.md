# Q658: blockhash_queue::get_max_age - hash considered valid past max_age

## Question
Can an unprivileged attacker who submits transactions referencing chosen recent blockhashes and durable nonces, holding a signed transaction until its blockhash is exactly at the max-age boundary, drive `blockhash_queue::get_max_age` to have is_hash_valid_for_age return true for a hash older than the configured max age, so that the invariant that a blockhash is valid for exactly max_age registrations and no longer is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `accounts-db/src/blockhash_queue.rs` -> `get_max_age`
- Entrypoint: submits transactions referencing chosen recent blockhashes and durable nonces, holding a signed transaction until its blockhash is exactly at the max-age boundary
- Attacker controls: which blockhash a transaction carries, resubmission timing across slots, and the nonce account it uses
- Exploit idea: Have is_hash_valid_for_age return true for a hash older than the configured max age.
- Invariant to test: A blockhash is valid for exactly max_age registrations and no longer.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the queue with the crafted hash/age sequence and assert validity and fee lookup match the expected slot window
