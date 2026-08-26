# Q2830: mem_pool::new - pool exhaustion changes execution outcome (aborting an invocation so buffers are)

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, aborting an invocation so buffers are returned on the unwind path, drive `mem_pool::new` to exhaust the pool so an allocation failure produces a different result on different nodes, so that the invariant that pool state never affects the result of a transaction is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `new`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, aborting an invocation so buffers are returned on the unwind path
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Exhaust the pool so an allocation failure produces a different result on different nodes.
- Invariant to test: Pool state never affects the result of a transaction.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
