# Q4023: mem_ops::memmove - overlap detection fails so memcpy corrupts data (using a length equal to the)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, using a length equal to the maximum account data size, drive `mem_ops::memmove` to make is_nonoverlapping return true for ranges that actually overlap, so that the invariant that overlapping ranges are rejected by memcpy and handled correctly by memmove is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `memmove`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, using a length equal to the maximum account data size
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Make is_nonoverlapping return true for ranges that actually overlap.
- Invariant to test: Overlapping ranges are rejected by memcpy and handled correctly by memmove.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
