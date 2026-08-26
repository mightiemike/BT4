# Q4401: bpf_loader::check_loader_id - close reclaims lamports from a program the attacker does not own (upgrading the program in the slot)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it, drive `bpf_loader::check_loader_id` to use common_close_account to drain a program or buffer account owned by someone else, so that the invariant that closing requires the account's authority and moves lamports only to the authorised recipient is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `check_loader_id`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, upgrading the program in the slot immediately before another transaction invokes it
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Use common_close_account to drain a program or buffer account owned by someone else.
- Invariant to test: Closing requires the account's authority and moves lamports only to the authorised recipient.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
