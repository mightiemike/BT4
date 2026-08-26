# Q3597: vm_addresses::RETURN_DATA_SCRATCHPAD - return data scratchpad readable across programs (targeting the boundary byte between two)

## Question
Can an unprivileged attacker who runs its own SBF program that computes guest addresses in the account, instruction and return-data regions, targeting the boundary byte between two adjacent regions, drive `vm_addresses::RETURN_DATA_SCRATCHPAD` to read the return data scratchpad region belonging to another program's invocation, so that the invariant that the return data scratchpad is scoped to the invocation that wrote it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/vm_addresses.rs` -> `RETURN_DATA_SCRATCHPAD`
- Entrypoint: runs its own SBF program that computes guest addresses in the account, instruction and return-data regions, targeting the boundary byte between two adjacent regions
- Attacker controls: every guest address it dereferences and the index of the account or instruction region it targets
- Exploit idea: Read the return data scratchpad region belonging to another program's invocation.
- Invariant to test: The return data scratchpad is scoped to the invocation that wrote it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test address computation for the crafted index and assert the resulting address stays inside its region
