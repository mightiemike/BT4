# Q603: status_cache::get_status_any_blockhash - panic on crafted key length (resubmitting a succeeded signature immediately after)

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced, drive `status_cache::get_status_any_blockhash` to supply a signature or key slice shorter than the cache key slice length so slicing panics during replay, so that the invariant that cache key extraction never indexes beyond the provided key is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `get_status_any_blockhash`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Supply a signature or key slice shorter than the cache key slice length so slicing panics during replay.
- Invariant to test: Cache key extraction never indexes beyond the provided key.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
