# Q4068: syscalls_sysvar::rust - cost not proportional to bytes copied (calling from the deepest permitted CPI)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, calling from the deepest permitted CPI level, drive `syscalls_sysvar::rust` to copy a large sysvar range for the base syscall cost only, so that the invariant that sysvar read cost is proportional to the bytes returned is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `syscalls/src/sysvar.rs` -> `rust`
- Entrypoint: invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, calling from the deepest permitted CPI level
- Attacker controls: the sysvar id, byte offset, length and destination pointer
- Exploit idea: Copy a large sysvar range for the base syscall cost only.
- Invariant to test: Sysvar read cost is proportional to the bytes returned.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test get_sysvar with the crafted offset/length and assert out-of-range reads are rejected
