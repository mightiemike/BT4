# Q4389: bpf_loader::check_loader_id - upgrade authority set to an address that never signed (listing the programdata account as writable)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction, drive `bpf_loader::check_loader_id` to set or transfer the upgrade authority to a key without that key's consent where consent is required, so that the invariant that authority transfers require the signatures the loader specifies is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `check_loader_id`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, listing the programdata account as writable in an unrelated transaction
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Set or transfer the upgrade authority to a key without that key's consent where consent is required.
- Invariant to test: Authority transfers require the signatures the loader specifies.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
