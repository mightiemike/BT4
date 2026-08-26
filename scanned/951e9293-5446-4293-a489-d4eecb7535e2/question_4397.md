# Q4397: bpf_loader::write_program_data - write into a buffer owned by another authority (upgrading the program in the slot)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it, drive `bpf_loader::write_program_data` to use write_program_data on a buffer whose authority the attacker does not hold, so that the invariant that buffer writes require the buffer authority's signature is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `write_program_data`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Use write_program_data on a buffer whose authority the attacker does not hold.
- Invariant to test: Buffer writes require the buffer authority's signature.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
