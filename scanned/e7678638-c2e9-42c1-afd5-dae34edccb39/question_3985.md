# Q3985: mem_ops::memmove - cost not charged proportional to bytes moved

## Question
Can an unprivileged attacker who invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, making source and destination differ by fewer bytes than the length, drive `mem_ops::memmove` to move a large region while mem_op_consume charges for a small one, so that the invariant that memory operation cost is proportional to bytes touched is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `syscalls/src/mem_ops.rs` -> `memmove`
- Entrypoint: invokes its own program which calls sol_memcpy, sol_memmove, sol_memset and sol_memcmp, making source and destination differ by fewer bytes than the length
- Attacker controls: source and destination guest pointers, lengths, and whether the ranges overlap
- Exploit idea: Move a large region while mem_op_consume charges for a small one.
- Invariant to test: Memory operation cost is proportional to bytes touched.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test the memory op with the crafted pointers and length and assert an access violation is raised
