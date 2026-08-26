# Q3750: syscalls::SyscallGetReturnData - return data read from another program's invocation (pointing every buffer argument at an)

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly, drive `syscalls::SyscallGetReturnData` to read return data through SyscallGetReturnData that a program outside the current call chain produced, so that the invariant that return data is scoped to the current invocation chain is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/lib.rs` -> `SyscallGetReturnData`
- Entrypoint: deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly
- Attacker controls: every syscall argument: guest pointers, lengths, seeds, curve points and loop counts
- Exploit idea: Read return data through SyscallGetReturnData that a program outside the current call chain produced.
- Invariant to test: Return data is scoped to the current invocation chain.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the syscall with the crafted arguments and asserting cost, bounds and determinism
