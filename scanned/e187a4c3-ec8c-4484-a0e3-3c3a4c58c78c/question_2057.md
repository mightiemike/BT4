# Q2057: serialization::fill_write - alignment padding overlaps the next account region (growing the account to the maximum)

## Question
Can an unprivileged attacker who invokes its own SBF program, whose input region is built by the parameter serializer, growing the account to the maximum permitted size in the same instruction, drive `serialization::fill_write` to choose data sizes so alignment padding causes adjacent regions to overlap, so that the invariant that serialized account regions never overlap is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/serialization.rs` -> `fill_write`
- Entrypoint: invokes its own SBF program, whose input region is built by the parameter serializer, growing the account to the maximum permitted size in the same instruction
- Attacker controls: the number of accounts, duplicate account indexes, data sizes, instruction data length and the loader ABI used
- Exploit idea: Choose data sizes so alignment padding causes adjacent regions to overlap.
- Invariant to test: Serialized account regions never overlap.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test serializing the crafted account set and asserting region bounds, alignment and deserialized deltas are correct
