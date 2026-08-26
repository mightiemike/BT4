# Q3992: mem_ops::is_nonoverlapping - overlap detection fails so memcpy corrupts data (targeting the last byte of one)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next, drive `mem_ops::is_nonoverlapping` to make is_nonoverlapping return true for ranges that actually overlap, so that the invariant that overlapping ranges are rejected by memcpy and handled correctly by memmove is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `is_nonoverlapping`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, targeting the last byte of one region and the first byte of the next
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Make is_nonoverlapping return true for ranges that actually overlap.
- Invariant to test: Overlapping ranges are rejected by memcpy and handled correctly by memmove.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
