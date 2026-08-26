# Q543: status_cache::max_root_entries - unbounded growth from attacker-chosen blockhashes

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity, drive `status_cache::max_root_entries` to flood the cache with entries under many distinct blockhashes so eviction destroys replay protection, so that the invariant that cache capacity limits never cause loss of replay protection for a live blockhash is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `max_root_entries`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Flood the cache with entries under many distinct blockhashes so eviction destroys replay protection.
- Invariant to test: Cache capacity limits never cause loss of replay protection for a live blockhash.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
