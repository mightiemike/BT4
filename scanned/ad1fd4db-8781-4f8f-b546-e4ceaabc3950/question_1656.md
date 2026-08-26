# Q1656: invoke_context::prepare_next_cpi_instruction - CPI callee gains a privilege the caller lacks

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, invoking through four levels of CPI so the deepest frame carries the fewest privileges, drive `invoke_context::prepare_next_cpi_instruction` to prepare a next-CPI instruction whose account privileges exceed those the caller holds, so that the invariant that a CPI callee's account privileges are a subset of the caller's is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `prepare_next_cpi_instruction`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, invoking through four levels of CPI so the deepest frame carries the fewest privileges
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Prepare a next-CPI instruction whose account privileges exceed those the caller holds.
- Invariant to test: A CPI callee's account privileges are a subset of the caller's.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
