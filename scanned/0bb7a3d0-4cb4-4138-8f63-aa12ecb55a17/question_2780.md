# Q2780: mem_pool::put - buffer returned to the pool while still referenced

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, alternating between the maximum and minimum heap requests across invocations, drive `mem_pool::put` to return a buffer via put while the VM still holds pointers into it, so that the invariant that a buffer is only returned to the pool after all references are dropped is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `put`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, alternating between the maximum and minimum heap requests across invocations
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Return a buffer via put while the VM still holds pointers into it.
- Invariant to test: A buffer is only returned to the pool after all references are dropped.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
