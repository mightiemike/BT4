# Q2172: memory_context::set_memory_context_abi_v1 - mutable mapping handed out during a readonly phase

## Question
Can an unprivileged attacker who runs its own SBF program whose memory mapping is installed and swapped by the memory context, performing CPI at the maximum permitted depth and returning an error from the deepest frame, drive `memory_context::set_memory_context_abi_v1` to acquire memory_mapping_mut while a caller frame still holds a shared reference, so that the invariant that at most one mutable mapping reference exists at a time is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/memory_context.rs` -> `set_memory_context_abi_v1`
- Entrypoint: runs its own SBF program whose memory mapping is installed and swapped by the memory context, performing CPI at the maximum permitted depth and returning an error from the deepest frame
- Attacker controls: the sequence of CPI pushes and pops and the account regions present at each level
- Exploit idea: Acquire memory_mapping_mut while a caller frame still holds a shared reference.
- Invariant to test: At most one mutable mapping reference exists at a time.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop sequence and assert the active mapping always matches the current invocation frame
