# Q2844: mem_pool::reset - recycled stack leaks caller frame contents (recursing to the maximum CPI depth)

## Question
Can an unprivileged attacker who invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, recursing to the maximum CPI depth and returning immediately, drive `mem_pool::reset` to read residual data from a stack buffer returned by get_stack, so that the invariant that stack buffers are cleared between invocations is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/mem_pool.rs` -> `reset`
- Entrypoint: invokes its own program repeatedly so VM stacks, heaps and call frames are recycled from the pool, recursing to the maximum CPI depth and returning immediately
- Attacker controls: heap size requests, invocation depth, and how many programs it invokes in one transaction
- Exploit idea: Read residual data from a stack buffer returned by get_stack.
- Invariant to test: Stack buffers are cleared between invocations.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the pool get/put sequence and assert every reissued buffer is zeroed and correctly sized
