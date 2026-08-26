# Q1785: invoke_context::get_check_aligned - alignment expectation flipped mid-execution (having the callee be a builtin)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program, drive `invoke_context::get_check_aligned` to make get_check_aligned return a value inconsistent with how parameters were serialized, so that the invariant that alignment rules are fixed by the loader that owns the executing program is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_check_aligned`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Make get_check_aligned return a value inconsistent with how parameters were serialized.
- Invariant to test: Alignment rules are fixed by the loader that owns the executing program.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
