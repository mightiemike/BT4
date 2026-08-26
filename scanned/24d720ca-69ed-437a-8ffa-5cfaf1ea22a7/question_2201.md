# Q2201: memory_context::new - stale mapping active after a frame pop (invoking a program compiled for the)

## Question
Can an unprivileged attacker who runs its own SBF program whose memory mapping is installed and swapped by the memory context, invoking a program compiled for the other loader ABI as the CPI callee, drive `memory_context::new` to make pop leave a mapping from a deeper frame active so translations resolve against the wrong regions, so that the invariant that the active memory mapping always belongs to the current invocation frame is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/memory_context.rs` -> `new`
- Entrypoint: runs its own SBF program whose memory mapping is installed and swapped by the memory context, invoking a program compiled for the other loader ABI as the CPI callee
- Attacker controls: the sequence of CPI pushes and pops and the account regions present at each level
- Exploit idea: Make pop leave a mapping from a deeper frame active so translations resolve against the wrong regions.
- Invariant to test: The active memory mapping always belongs to the current invocation frame.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the push/pop sequence and assert the active mapping always matches the current invocation frame
