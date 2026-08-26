# Q568: status_cache::add_root - entry purged while still replay-relevant (resubmitting a succeeded signature immediately after)

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced, drive `status_cache::add_root` to get an executed signature purged by root or max-age handling while its blockhash is still valid, so that the invariant that a signature stays in the cache for at least as long as its blockhash can be reused is broken and the outcome is Loss of Funds (double-spend / replayed transfer)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `add_root`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Get an executed signature purged by root or max-age handling while its blockhash is still valid.
- Invariant to test: A signature stays in the cache for at least as long as its blockhash can be reused.
- Expected Immunefi impact: Critical - Loss of Funds (double-spend / replayed transfer)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
