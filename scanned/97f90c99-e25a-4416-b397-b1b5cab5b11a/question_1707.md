# Q1707: invoke_context::native_invoke_signed - PDA signer authority forged (passing the same account twice with)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction, drive `invoke_context::native_invoke_signed` to obtain is_signer on an address that is not a PDA of the invoking program, so that the invariant that only PDAs derived from the invoking program id can be signed via CPI seeds is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `native_invoke_signed`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Obtain is_signer on an address that is not a PDA of the invoking program.
- Invariant to test: Only PDAs derived from the invoking program id can be signed via CPI seeds.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
