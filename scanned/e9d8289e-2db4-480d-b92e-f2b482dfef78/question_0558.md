# Q558: status_cache::purge_roots - root ordering assumption broken

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity, drive `status_cache::purge_roots` to add roots out of order so purge_roots removes a still-needed root set, so that the invariant that root bookkeeping is monotone and never discards an unrooted-but-live slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `purge_roots`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Add roots out of order so purge_roots removes a still-needed root set.
- Invariant to test: Root bookkeeping is monotone and never discards an unrooted-but-live slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
