# Q596: status_cache::add_root - root ordering assumption broken (resubmitting a succeeded signature immediately after)

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced, drive `status_cache::add_root` to add roots out of order so purge_roots removes a still-needed root set, so that the invariant that root bookkeeping is monotone and never discards an unrooted-but-live slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `add_root`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Add roots out of order so purge_roots removes a still-needed root set.
- Invariant to test: Root bookkeeping is monotone and never discards an unrooted-but-live slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
