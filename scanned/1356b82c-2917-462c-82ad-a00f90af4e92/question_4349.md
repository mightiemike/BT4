# Q4349: bpf_loader::process_loader_upgradeable_instruction - closed program's address reused to hijack callers (closing and redeploying at the same)

## Question
Can an unprivileged attacker who deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, closing and redeploying at the same address within one block, drive `bpf_loader::process_loader_upgradeable_instruction` to close a program and create a new one at the same address so existing CPIs execute attacker code, so that the invariant that a program address that has been closed cannot be reused for new executable bytecode is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `process_loader_upgradeable_instruction`
- Entrypoint: deploys, writes, extends, upgrades and closes its own programs through the upgradeable loader, closing and redeploying at the same address within one block
- Attacker controls: buffer contents, authority keys, program and programdata account layouts, and instruction ordering
- Exploit idea: Close a program and create a new one at the same address so existing CPIs execute attacker code.
- Invariant to test: A program address that has been closed cannot be reused for new executable bytecode.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the loader instruction against the crafted accounts and assert authority and state checks reject it
