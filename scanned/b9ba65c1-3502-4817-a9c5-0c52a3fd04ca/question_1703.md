# Q1703: invoke_context::native_invoke_signed - CPI callee gains a privilege the caller lacks (passing the same account twice with)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction, drive `invoke_context::native_invoke_signed` to prepare a next-CPI instruction whose account privileges exceed those the caller holds, so that the invariant that a CPI callee's account privileges are a subset of the caller's is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `native_invoke_signed`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Prepare a next-CPI instruction whose account privileges exceed those the caller holds.
- Invariant to test: A CPI callee's account privileges are a subset of the caller's.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
