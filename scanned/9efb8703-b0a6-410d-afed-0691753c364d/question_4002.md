# Q4002: mem_ops::memmove - write into a readonly account region (targeting the last byte of one)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next, drive `mem_ops::memmove` to target a readonly account's region as the destination of a memory operation, so that the invariant that memory operations only write to writable regions is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `memmove`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Target a readonly account's region as the destination of a memory operation.
- Invariant to test: Memory operations only write to writable regions.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
