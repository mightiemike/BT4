# Q4019: mem_ops::memmove - cross-region copy leaks or corrupts another account (performing the copy from inside a)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, performing the copy from inside a CPI callee on the caller's account, drive `mem_ops::memmove` to copy between two different accounts' regions in a single operation, so that the invariant that a memory operation stays within one region on each side is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `memmove`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, performing the copy from inside a CPI callee on the caller's account
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Copy between two different accounts' regions in a single operation.
- Invariant to test: A memory operation stays within one region on each side.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
