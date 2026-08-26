# Q1791: invoke_context::get_log_collector - log collector unbounded from inside a program (having the callee be a builtin)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program, drive `invoke_context::get_log_collector` to emit log data whose accounting does not consume compute units proportional to bytes written, so that the invariant that log output is charged compute units proportional to its size is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_log_collector`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, having the callee be a builtin program rather than another BPF program
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Emit log data whose accounting does not consume compute units proportional to bytes written.
- Invariant to test: Log output is charged compute units proportional to its size.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
