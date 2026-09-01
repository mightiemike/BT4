# Q1013: trace of a system transaction via `get_traces_with_requested_tracer_and_config` (trace.rs)

## Question
Can an unprivileged attacker who calls `debug_traceTransaction` / `debug_traceCall` with a custom tracer config, controlling tracer config and call overrides, drive `get_traces_with_requested_tracer_and_config` in `crates/ethereum-rpc/src/trace.rs` so that the system transaction a trace exposes and the system transaction the block executed stop being the same, breaking the invariant that traces reflect executed system transactions exactly?

## Target
- File/function: `crates/ethereum-rpc/src/trace.rs` -> `get_traces_with_requested_tracer_and_config`
- Entrypoint: unprivileged party calls `debug_traceTransaction` / `debug_traceCall` with a custom tracer config
- Attacker controls: tracer config and call overrides
- Exploit idea: trace of a system transaction - reach `get_traces_with_requested_tracer_and_config` from that entrypoint and force the divergence where the system transaction a trace exposes and the system transaction the block executed stop being the same; the adjacent symbols in the same file that carry the value are `handle_debug_trace_chain`, `debug_trace_by_block_number`, `apply_call_config`, `remove_logs_from_call_frame`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: traces reflect executed system transactions exactly
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: trace a deposit block and diff against execution
