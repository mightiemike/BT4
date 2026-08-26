# Q3755: syscalls::translate_slice - sibling instruction inspection leaks or forges privileges (pointing every buffer argument at an)

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly, drive `syscalls::translate_slice` to use SyscallGetProcessedSiblingInstruction to observe or assert privileges that do not match the transaction, so that the invariant that sibling instruction data reported matches the executed instruction exactly is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/lib.rs` -> `translate_slice`
- Entrypoint: deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly
- Attacker controls: every syscall argument: guest pointers, lengths, seeds, curve points and loop counts
- Exploit idea: Use SyscallGetProcessedSiblingInstruction to observe or assert privileges that do not match the transaction.
- Invariant to test: Sibling instruction data reported matches the executed instruction exactly.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the syscall with the crafted arguments and asserting cost, bounds and determinism
