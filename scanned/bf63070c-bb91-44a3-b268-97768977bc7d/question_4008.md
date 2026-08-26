# Q4008: mem_ops::memmove - overlap detection fails so memcpy corrupts data (performing the copy from inside a)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, performing the copy from inside a CPI callee on the caller's account, drive `mem_ops::memmove` to make is_nonoverlapping return true for ranges that actually overlap, so that the invariant that overlapping ranges are rejected by memcpy and handled correctly by memmove is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `memmove`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, performing the copy from inside a CPI callee on the caller's account
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Make is_nonoverlapping return true for ranges that actually overlap.
- Invariant to test: Overlapping ranges are rejected by memcpy and handled correctly by memmove.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
