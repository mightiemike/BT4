# Q4407: bpf_loader::process_loader_upgradeable_instruction - bytecode replaced without re-verification (upgrading the program in the slot)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it, drive `bpf_loader::process_loader_upgradeable_instruction` to install new bytecode that is never passed through ELF verification, so that the invariant that every byte of executable programdata has been verified is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `process_loader_upgradeable_instruction`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Install new bytecode that is never passed through ELF verification.
- Invariant to test: Every byte of executable programdata has been verified.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
