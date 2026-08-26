# Q581: status_cache::roots - ancestor filtering allows cross-fork replay (resubmitting a succeeded signature immediately after)

## Question
Can an unprivileged attacker who submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced, drive `status_cache::roots` to get a status recorded on a sibling fork to satisfy (or fail) the duplicate check on this fork, so that the invariant that duplicate detection considers exactly the ancestors of the querying bank is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/status_cache.rs` -> `roots`
- Entrypoint: submits transactions repeatedly, choosing signatures, blockhashes and timing across slot and root boundaries, resubmitting a succeeded signature immediately after a root is advanced
- Attacker controls: the transaction signature bytes, the recent blockhash chosen, and when the transaction is resubmitted
- Exploit idea: Get a status recorded on a sibling fork to satisfy (or fail) the duplicate check on this fork.
- Invariant to test: Duplicate detection considers exactly the ancestors of the querying bank.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the status cache with the crafted key/slot sequence and assert the duplicate lookup returns the prior status
