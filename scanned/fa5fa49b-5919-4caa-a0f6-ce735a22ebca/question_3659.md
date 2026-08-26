# Q3659: syscalls::SyscallAllocFree - allocator hands out overlapping regions

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, calling the syscall from the deepest permitted CPI level, drive `syscalls::SyscallAllocFree` to call SyscallAllocFree so two allocations overlap or an allocation escapes the heap region, so that the invariant that allocations are disjoint and contained within the granted heap is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/lib.rs` -> `SyscallAllocFree`
- Entrypoint: deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, calling the syscall from the deepest permitted CPI level
- Attacker controls: every syscall argument: guest pointers, lengths, seeds, curve points and loop counts
- Exploit idea: Call SyscallAllocFree so two allocations overlap or an allocation escapes the heap region.
- Invariant to test: Allocations are disjoint and contained within the granted heap.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the syscall with the crafted arguments and asserting cost, bounds and determinism
