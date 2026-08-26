# Q549: status_cache::add_root - clear_slot_entries on a fork removes protection on another

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity, drive `status_cache::add_root` to trigger slot-entry clearing that removes statuses still needed by a surviving fork, so that the invariant that clearing entries for a dead slot never affects a live fork's replay protection is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `add_root`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, submitting thousands of transactions under distinct blockhashes to pressure cache capacity
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Trigger slot-entry clearing that removes statuses still needed by a surviving fork.
- Invariant to test: Clearing entries for a dead slot never affects a live fork's replay protection.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
