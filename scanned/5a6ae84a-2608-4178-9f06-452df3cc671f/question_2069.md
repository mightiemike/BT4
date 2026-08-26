# Q2069: serialization::serialize_parameters - instruction data region writable from the program (growing the account to the maximum)

## Question
Can an unprivileged attacker who invokes its own SBF program, whose input region is built by the parameter serializer, growing the account to the maximum permitted size in the same instruction, drive `serialization::serialize_parameters` to obtain a writable mapping over the instruction data region and mutate it mid-execution, so that the invariant that instruction data is immutable for the duration of the instruction is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/serialization.rs` -> `serialize_parameters`
- Entrypoint: invokes its own SBF program, whose input region is built by the parameter serializer, growing the account to the maximum permitted size in the same instruction
- Attacker controls: the number of accounts, duplicate account indexes, data sizes, instruction data length and the loader ABI used
- Exploit idea: Obtain a writable mapping over the instruction data region and mutate it mid-execution.
- Invariant to test: Instruction data is immutable for the duration of the instruction.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test serializing the crafted account set and asserting region bounds, alignment and deserialized deltas are correct
