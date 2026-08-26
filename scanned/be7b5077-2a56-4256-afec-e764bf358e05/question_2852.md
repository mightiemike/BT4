# Q2852: mem_pool::put - call frame buffer reused across nesting levels (recursing to the maximum CPI depth)

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, recursing to the maximum CPI depth and returning immediately, drive `mem_pool::put` to make get_call_frames hand the same buffer to two live invocation levels, so that the invariant that each live invocation level holds a distinct call frame buffer is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `put`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, recursing to the maximum CPI depth and returning immediately
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Make get_call_frames hand the same buffer to two live invocation levels.
- Invariant to test: Each live invocation level holds a distinct call frame buffer.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
