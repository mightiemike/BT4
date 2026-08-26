# Q1715: invoke_context::get_remaining - compute units not consumed for work performed (passing the same account twice with)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction, drive `invoke_context::get_remaining` to perform metered work while consume or consume_checked deducts nothing, so that the invariant that every unit of metered work deducts from the remaining compute budget is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_remaining`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Perform metered work while consume or consume_checked deducts nothing.
- Invariant to test: Every unit of metered work deducts from the remaining compute budget.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
