# Q3997: mem_ops::mem_op_consume - length arithmetic overflows the bounds check (targeting the last byte of one)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next, drive `mem_ops::mem_op_consume` to choose a length whose addition to the pointer wraps so the check passes, so that the invariant that pointer plus length is computed with checked arithmetic is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `mem_op_consume`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Choose a length whose addition to the pointer wraps so the check passes.
- Invariant to test: Pointer plus length is computed with checked arithmetic.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
