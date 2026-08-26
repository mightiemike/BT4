# Q2032: serialization::modify_memory_region_of_account - readonly account serialized into a writable region (sizing one account so the next)

## Question
Can an unprivileged attacker who invokes its own SBF program, whose input region is built by the parameter serializer, sizing one account so the next region starts on an unaligned boundary, drive `serialization::modify_memory_region_of_account` to get an account the message marked readonly published in a writable memory region, so that the invariant that region writability matches the message's account privileges is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/serialization.rs` -> `modify_memory_region_of_account`
- Entrypoint: invokes its own SBF program, whose input region is built by the parameter serializer, sizing one account so the next region starts on an unaligned boundary
- Attacker controls: the number of accounts, duplicate account indexes, data sizes, instruction data length and the loader ABI used
- Exploit idea: Get an account the message marked readonly published in a writable memory region.
- Invariant to test: Region writability matches the message's account privileges.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test serializing the crafted account set and asserting region bounds, alignment and deserialized deltas are correct
