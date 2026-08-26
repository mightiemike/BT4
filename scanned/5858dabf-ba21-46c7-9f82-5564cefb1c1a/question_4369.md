# Q4369: bpf_loader::process_loader_upgradeable_instruction - close reclaims lamports from a program the attacker does not own (listing the programdata account as writable)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction, drive `bpf_loader::process_loader_upgradeable_instruction` to use common_close_account to drain a program or buffer account owned by someone else, so that the invariant that closing requires the account's authority and moves lamports only to the authorised recipient is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `process_loader_upgradeable_instruction`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Use common_close_account to drain a program or buffer account owned by someone else.
- Invariant to test: Closing requires the account's authority and moves lamports only to the authorised recipient.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
