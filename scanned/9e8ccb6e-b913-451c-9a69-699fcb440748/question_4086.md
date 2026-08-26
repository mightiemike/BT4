# Q4086: syscalls_logging::rust - log message read past its declared length (logging immediately before the compute budget)

## Question
Can an unprivileged attacker who invokes its own program which emits logs through the logging syscalls, logging immediately before the compute budget is exhausted, drive `syscalls_logging::rust` to pass a length longer than the mapped region so the collector reads adjacent memory, so that the invariant that log message length is validated against the mapped region is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/logging.rs` -> `rust`
- Entrypoint: invokes its own program which emits logs through the logging syscalls, logging immediately before the compute budget is exhausted
- Attacker controls: log message contents, lengths, and how many log calls it makes per instruction
- Exploit idea: Pass a length longer than the mapped region so the collector reads adjacent memory.
- Invariant to test: Log message length is validated against the mapped region.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the logging syscall with the crafted message and assert compute units consumed scale with bytes
