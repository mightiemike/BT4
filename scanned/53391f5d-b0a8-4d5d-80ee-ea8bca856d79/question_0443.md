# Q0443: trace cache poisoning by config via `apply_call_config` (trace.rs)

## Question
Can an unprivileged attacker who calls `debug_traceTransaction` / `debug_traceCall` with a custom tracer config, controlling the traced contract's bytecode, drive `apply_call_config` in `crates/ethereum-rpc/src/trace.rs` so that the trace produced under one tracer config and the trace produced for the same transaction under another stop being the same execution, breaking the invariant that a transaction's trace is a function of the transaction and the chain, not of the request?

## Target
- File/function: `crates/ethereum-rpc/src/trace.rs` -> `apply_call_config`
- Entrypoint: unprivileged party calls `debug_traceTransaction` / `debug_traceCall` with a custom tracer config
- Attacker controls: the traced contract's bytecode
- Exploit idea: trace cache poisoning by config - reach `apply_call_config` from that entrypoint and force the divergence where the trace produced under one tracer config and the trace produced for the same transaction under another stop being the same execution; the adjacent symbols in the same file that carry the value are `handle_debug_trace_chain`, `debug_trace_by_block_number`, `remove_logs_from_call_frame`, `get_traces_with_requested_tracer_and_config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a transaction's trace is a function of the transaction and the chain, not of the request
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: trace the same tx with varying configs and diff the state-changing effects
