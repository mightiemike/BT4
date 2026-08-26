# Q2770: mem_pool::new - buffer returned smaller than the requested heap

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, alternating between the maximum and minimum heap requests across invocations, drive `mem_pool::new` to request a heap larger than the pooled buffer and receive the smaller one without error, so that the invariant that a pooled buffer is at least as large as the requested size is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `new`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, alternating between the maximum and minimum heap requests across invocations
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Request a heap larger than the pooled buffer and receive the smaller one without error.
- Invariant to test: A pooled buffer is at least as large as the requested size.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
