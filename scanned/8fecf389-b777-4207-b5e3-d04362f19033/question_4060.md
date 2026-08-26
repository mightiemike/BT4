# Q4060: syscalls_sysvar::rust - destination pointer not validated as writable (reading the slot hashes sysvar with)

## Question
Can an unprivileged attacker who invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, reading the slot hashes sysvar with a length larger than its buffer, drive `syscalls_sysvar::rust` to write the sysvar bytes into a readonly or unmapped destination, so that the invariant that the destination is translated as writable before the copy is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `syscalls/src/sysvar.rs` -> `rust`
- Entrypoint: invokes its own program which calls sol_get_sysvar with chosen ids, offsets and lengths, reading the slot hashes sysvar with a length larger than its buffer
- Attacker controls: the sysvar id, byte offset, length and destination pointer
- Exploit idea: Write the sysvar bytes into a readonly or unmapped destination.
- Invariant to test: The destination is translated as writable before the copy.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test get_sysvar with the crafted offset/length and assert out-of-range reads are rejected
