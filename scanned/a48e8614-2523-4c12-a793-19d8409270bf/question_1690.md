# Q1690: invoke_context::get_feature_set - feature set observed mid-transaction changes behaviour

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, invoking through four levels of CPI so the deepest frame carries the fewest privileges, drive `invoke_context::get_feature_set` to make get_feature_set or a feature predicate return different values within one transaction or across nodes, so that the invariant that the active feature set is constant for the whole slot on every node is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_feature_set`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, invoking through four levels of CPI so the deepest frame carries the fewest privileges
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Make get_feature_set or a feature predicate return different values within one transaction or across nodes.
- Invariant to test: The active feature set is constant for the whole slot on every node.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
