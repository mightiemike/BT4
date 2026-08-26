# Q529: status_cache::max_root_entries - entry purged while still replay-relevant

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity, drive `status_cache::max_root_entries` to get an executed signature purged by root or max-age handling while its blockhash is still valid, so that the invariant that a signature stays in the cache for at least as long as its blockhash can be reused is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `max_root_entries`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Get an executed signature purged by root or max-age handling while its blockhash is still valid.
- Invariant to test: A signature stays in the cache for at least as long as its blockhash can be reused.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
