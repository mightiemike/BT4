# Q669: blockhash_queue::get_max_age - purge removes a hash still referenced by pending transactions

## Question
Can an unprivileged attacker who submits transactions referencing chosen recent blockhashes and durable nonces, holding a signed transaction until its blockhash is exactly at the max-age boundary, drive `blockhash_queue::get_max_age` to force purge to drop a hash whose transactions are still in flight so replay protection or fees break, so that the invariant that purge only removes hashes that can no longer validate any transaction is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `accounts-db/src/blockhash_queue.rs` -> `get_max_age`
- Entrypoint: submits transactions referencing chosen recent blockhashes and durable nonces, holding a signed transaction until its blockhash is exactly at the max-age boundary
- Attacker controls: which blockhash a transaction carries, resubmission timing across slots, and the nonce account it uses
- Exploit idea: Force purge to drop a hash whose transactions are still in flight so replay protection or fees break.
- Invariant to test: Purge only removes hashes that can no longer validate any transaction.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the queue with the crafted hash/age sequence and assert validity and fee lookup match the expected slot window
