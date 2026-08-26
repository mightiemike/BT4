# Q2225: memory_context::memory_mapping - mutable mapping handed out during a readonly phase (passing a pointer obtained in a)

## Question
Can an unprivileged attacker who runs its own SBF program whose memory mapping is installed and swapped by the memory context, passing a pointer obtained in a deeper frame back to the caller frame, drive `memory_context::memory_mapping` to acquire memory_mapping_mut while a caller frame still holds a shared reference, so that the invariant that at most one mutable mapping reference exists at a time is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/memory_context.rs` -> `memory_mapping`
- Entrypoint: runs its own SBF program whose memory mapping is installed and swapped by the memory context, passing a pointer obtained in a deeper frame back to the caller frame
- Attacker controls: the sequence of CPI pushes and pops and the account regions present at each level
- Exploit idea: Acquire memory_mapping_mut while a caller frame still holds a shared reference.
- Invariant to test: At most one mutable mapping reference exists at a time.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop sequence and assert the active mapping always matches the current invocation frame
