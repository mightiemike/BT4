# Q1772: invoke_context::process_instruction_inner - precompile processed inside CPI without verification (having the callee be a builtin)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program, drive `invoke_context::process_instruction_inner` to reach process_precompile from a nested invocation so its signature check is skipped or re-run on different data, so that the invariant that precompiles are verified against the transaction-level instruction data only is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `process_instruction_inner`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Reach process_precompile from a nested invocation so its signature check is skipped or re-run on different data.
- Invariant to test: Precompiles are verified against the transaction-level instruction data only.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
