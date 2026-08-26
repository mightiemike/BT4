# Q587: status_cache::clear - unbounded growth from attacker-chosen blockhashes (resubmitting a succeeded signature immediately after)

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced, drive `status_cache::clear` to flood the cache with entries under many distinct blockhashes so eviction destroys replay protection, so that the invariant that cache capacity limits never cause loss of replay protection for a live blockhash is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `clear`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Flood the cache with entries under many distinct blockhashes so eviction destroys replay protection.
- Invariant to test: Cache capacity limits never cause loss of replay protection for a live blockhash.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
