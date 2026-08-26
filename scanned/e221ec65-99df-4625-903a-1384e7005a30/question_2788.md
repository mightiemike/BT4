# Q2788: mem_pool::reset - recycled heap retains a previous program's data (invoking many distinct programs within one)

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, invoking many distinct programs within one transaction, drive `mem_pool::reset` to obtain a heap buffer from get_heap that still holds bytes written by an earlier invocation, so that the invariant that every pooled buffer is zeroed before reuse is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `reset`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, invoking many distinct programs within one transaction
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Obtain a heap buffer from get_heap that still holds bytes written by an earlier invocation.
- Invariant to test: Every pooled buffer is zeroed before reuse.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
