# Q3634: syscalls::translate_and_check_program_address_inputs - program address derivation returns an address off the curve rule

## Question
Can an unprivileged attacker who deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, calling the syscall from the deepest permitted CPI level, drive `syscalls::translate_and_check_program_address_inputs` to make SyscallCreateProgramAddress or SyscallTryFindProgramAddress return an address that is a valid keypair public key, so that the invariant that derived program addresses are never valid ed25519 public keys is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/lib.rs` -> `translate_and_check_program_address_inputs`
- Entrypoint: deploys and invokes its own SBF program that calls syscalls with attacker-chosen arguments, calling the syscall from the deepest permitted CPI level
- Attacker controls: every syscall argument: guest pointers, lengths, seeds, curve points and loop counts
- Exploit idea: Make SyscallCreateProgramAddress or SyscallTryFindProgramAddress return an address that is a valid keypair public key.
- Invariant to test: Derived program addresses are never valid ed25519 public keys.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the syscall with the crafted arguments and asserting cost, bounds and determinism
