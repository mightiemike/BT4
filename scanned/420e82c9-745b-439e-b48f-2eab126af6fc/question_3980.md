# Q3980: mem_ops::rust - length arithmetic overflows the bounds check

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, making source and destination differ by fewer bytes than the length, drive `mem_ops::rust` to choose a length whose addition to the pointer wraps so the check passes, so that the invariant that pointer plus length is computed with checked arithmetic is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `rust`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, making source and destination differ by fewer bytes than the length
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Choose a length whose addition to the pointer wraps so the check passes.
- Invariant to test: Pointer plus length is computed with checked arithmetic.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
