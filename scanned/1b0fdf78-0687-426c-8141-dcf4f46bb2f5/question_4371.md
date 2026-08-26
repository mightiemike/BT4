# Q4371: bpf_loader::common_extend_program - extend grows programdata without paying rent (listing the programdata account as writable)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction, drive `bpf_loader::common_extend_program` to use common_extend_program to grow programdata while leaving it rent-paying or under-funded, so that the invariant that extension requires sufficient lamports to remain rent-exempt is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `common_extend_program`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Use common_extend_program to grow programdata while leaving it rent-paying or under-funded.
- Invariant to test: Extension requires sufficient lamports to remain rent-exempt.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
