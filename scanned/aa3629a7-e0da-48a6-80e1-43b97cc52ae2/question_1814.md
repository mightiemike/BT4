# Q1814: invoke_context::get_stack_height - pop without matching push corrupts the frame stack (deploying the callee program in the)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, deploying the callee program in the same slot as the invoking transaction, drive `invoke_context::get_stack_height` to cause pop to unwind a frame that push never created so the parent frame's privileges are inherited, so that the invariant that each pop corresponds to exactly one push at the same nesting level is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_stack_height`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, deploying the callee program in the same slot as the invoking transaction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Cause pop to unwind a frame that push never created so the parent frame's privileges are inherited.
- Invariant to test: Each pop corresponds to exactly one push at the same nesting level.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
