# Q1816: invoke_context::process_precompile - precompile processed inside CPI without verification (deploying the callee program in the)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, deploying the callee program in the same slot as the invoking transaction, drive `invoke_context::process_precompile` to reach process_precompile from a nested invocation so its signature check is skipped or re-run on different data, so that the invariant that precompiles are verified against the transaction-level instruction data only is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `process_precompile`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, deploying the callee program in the same slot as the invoking transaction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Reach process_precompile from a nested invocation so its signature check is skipped or re-run on different data.
- Invariant to test: Precompiles are verified against the transaction-level instruction data only.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
