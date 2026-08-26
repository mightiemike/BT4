# Q2017: serialization::deserialize_parameters_for_abiv0 - deserialization applies a resize the runtime did not authorize (sizing one account so the next)

## Question
Can an unprivileged attacker who invokes its own SBF program, whose input region is built by the parameter serializer, sizing one account so the next region starts on an unaligned boundary, drive `serialization::deserialize_parameters_for_abiv0` to have deserialize_parameters read back a data length the account was never permitted to reach, so that the invariant that post-execution data length is within the authorized resize delta is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/serialization.rs` -> `deserialize_parameters_for_abiv0`
- Entrypoint: invokes its own SBF program, whose input region is built by the parameter serializer, sizing one account so the next region starts on an unaligned boundary
- Attacker controls: the number of accounts, duplicate account indexes, data sizes, instruction data length and the loader ABI used
- Exploit idea: Have deserialize_parameters read back a data length the account was never permitted to reach.
- Invariant to test: Post-execution data length is within the authorized resize delta.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test serializing the crafted account set and asserting region bounds, alignment and deserialized deltas are correct
