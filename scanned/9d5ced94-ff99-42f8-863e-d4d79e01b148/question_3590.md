# Q3590: vm_addresses::GUEST_ACCOUNT_PAYLOAD_BASE_ADDRESS - account index times region size overflows (targeting the boundary byte between two)

## Question
Can an unprivileged attacker who runs its own SBF program that computes guest addresses in the account, instruction and return-data regions, targeting the boundary byte between two adjacent regions, drive `vm_addresses::GUEST_ACCOUNT_PAYLOAD_BASE_ADDRESS` to choose an account index whose product with GUEST_REGION_SIZE wraps into another region's address space, so that the invariant that region base address computation is checked and stays within the guest address space is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/vm_addresses.rs` -> `GUEST_ACCOUNT_PAYLOAD_BASE_ADDRESS`
- Entrypoint: runs its own SBF program that computes guest addresses in the account, instruction and return-data regions, targeting the boundary byte between two adjacent regions
- Attacker controls: every guest address it dereferences and the index of the account or instruction region it targets
- Exploit idea: Choose an account index whose product with GUEST_REGION_SIZE wraps into another region's address space.
- Invariant to test: Region base address computation is checked and stays within the guest address space.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test address computation for the crafted index and assert the resulting address stays inside its region
