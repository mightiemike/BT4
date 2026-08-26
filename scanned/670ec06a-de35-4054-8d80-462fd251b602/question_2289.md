# Q2289: execution_budget::get_max_instruction_stack_depth - stack depth maximum inconsistent with what is enforced (chaining CPIs until the configured stack)

## Question
Can an unprivileged attacker who submits a transaction that declares compute limits and then executes its own program under them, chaining CPIs until the configured stack depth is reached, drive `execution_budget::get_max_instruction_stack_depth` to obtain an invocation depth beyond get_max_instruction_stack_depth's value, so that the invariant that the enforced stack depth equals the configured maximum on every node is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `program-runtime/src/execution_budget.rs` -> `get_max_instruction_stack_depth`
- Entrypoint: submits a transaction that declares compute limits and then executes its own program under them, chaining CPIs until the configured stack depth is reached
- Attacker controls: the requested compute unit limit, heap size, stack depth via CPI, and syscall usage
- Exploit idea: Obtain an invocation depth beyond get_max_instruction_stack_depth's value.
- Invariant to test: The enforced stack depth equals the configured maximum on every node.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: unit-test the budget configuration and assert every granted resource has a matching charged cost
