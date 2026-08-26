# Q3280: instruction_context::get_instruction_data - VM slice configuration points outside the instruction region (invoking the instruction from a CPI)

## Question
Can an unprivileged attacker who invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep, drive `instruction_context::get_instruction_data` to make configure_vm_slices publish slices that overlap other instructions' regions, so that the invariant that VM slices for an instruction cover only that instruction's own regions is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `transaction-context/src/instruction.rs` -> `get_instruction_data`
- Entrypoint: invokes its own program with a crafted instruction account list, invoking the instruction from a CPI callee two levels deep
- Attacker controls: instruction account indexes, duplicate entries, signer and writable flags, instruction data and program index
- Exploit idea: Make configure_vm_slices publish slices that overlap other instructions' regions.
- Invariant to test: VM slices for an instruction cover only that instruction's own regions.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the instruction context accessors against the crafted account list and assert privileges and indexes are exact
