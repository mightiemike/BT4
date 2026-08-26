# Q3718: syscalls::SyscallTryFindProgramAddress - program address derivation returns an address off the curve rule (pointing every buffer argument at an)

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly, drive `syscalls::SyscallTryFindProgramAddress` to make SyscallCreateProgramAddress or SyscallTryFindProgramAddress return an address that is a valid keypair public key, so that the invariant that derived program addresses are never valid ed25519 public keys is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/lib.rs` -> `SyscallTryFindProgramAddress`
- Entrypoint: deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, pointing every buffer argument at an account region the instruction marked readonly
- Attacker controls: every syscall argument: guest pointers, lengths, seeds, curve points and loop counts
- Exploit idea: Make SyscallCreateProgramAddress or SyscallTryFindProgramAddress return an address that is a valid keypair public key.
- Invariant to test: Derived program addresses are never valid ed25519 public keys.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the syscall with the crafted arguments and asserting cost, bounds and determinism
