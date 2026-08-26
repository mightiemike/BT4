# Q2090: serialization::write - buffer write past the allocated input region (using the deprecated loader ABI for)

## Question
Can an unprivileged attacker who invokes its own SBF program, whose input region is built by the parameter serializer, using the deprecated loader ABI for the invoked program, drive `serialization::write` to drive write, write_all or fill_write past the reserved input buffer length, so that the invariant that all serializer writes stay inside the pre-computed buffer size is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `program-runtime/src/serialization.rs` -> `write`
- Entrypoint: invokes its own SBF program, whose input region is built by the parameter serializer, using the deprecated loader ABI for the invoked program
- Attacker controls: the number of accounts, duplicate account indexes, data sizes, instruction data length and the loader ABI used
- Exploit idea: Drive write, write_all or fill_write past the reserved input buffer length.
- Invariant to test: All serializer writes stay inside the pre-computed buffer size.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: program-runtime unit test serializing the crafted account set and asserting region bounds, alignment and deserialized deltas are correct
