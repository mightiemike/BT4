# Q4043: syscalls_sysvar::get_sysvar - offset plus length reads past the sysvar buffer (reading the epoch rewards sysvar during)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, reading the epoch rewards sysvar during the distribution window, drive `syscalls_sysvar::get_sysvar` to request a range extending beyond the sysvar's serialized length, so that the invariant that sysvar reads are bounded by the sysvar's serialized size is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `syscalls/src/sysvar.rs` -> `get_sysvar`
- Entrypoint: invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, reading the epoch rewards sysvar during the distribution window
- Attacker controls: the sysvar id, byte offset, length and destination pointer
- Exploit idea: Request a range extending beyond the sysvar's serialized length.
- Invariant to test: Sysvar reads are bounded by the sysvar's serialized size.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test get_sysvar with the crafted offset/length and assert out-of-range reads are rejected
