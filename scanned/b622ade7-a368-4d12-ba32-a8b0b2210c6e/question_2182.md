# Q2182: memory_context::memory_mapping - stale mapping active after a frame pop (resizing an account inside the deepest)

## Question
Can an unprivileged attacker who runs its own SBF program whose memory mapping is installed and swapped by the memory context, resizing an account inside the deepest frame before popping back, drive `memory_context::memory_mapping` to make pop leave a mapping from a deeper frame active so translations resolve against the wrong regions, so that the invariant that the active memory mapping always belongs to the current invocation frame is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/memory_context.rs` -> `memory_mapping`
- Entrypoint: runs its own SBF program whose memory mapping is installed and swapped by the memory context, resizing an account inside the deepest frame before popping back
- Attacker controls: the sequence of CPI pushes and pops and the account regions present at each level
- Exploit idea: Make pop leave a mapping from a deeper frame active so translations resolve against the wrong regions.
- Invariant to test: The active memory mapping always belongs to the current invocation frame.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop sequence and assert the active mapping always matches the current invocation frame
