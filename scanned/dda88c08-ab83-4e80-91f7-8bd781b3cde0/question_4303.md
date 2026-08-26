# Q4303: bpf_loader::check_loader_id - upgrade performed without the upgrade authority

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, invoking the loader instruction through CPI from its own program, drive `bpf_loader::check_loader_id` to upgrade a program whose upgrade authority did not sign, so that the invariant that only the current upgrade authority may replace program bytecode is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `check_loader_id`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, invoking the loader instruction through CPI from its own program
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Upgrade a program whose upgrade authority did not sign.
- Invariant to test: Only the current upgrade authority may replace program bytecode.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
