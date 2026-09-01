# Q0132: constant drift between roles via `types` (types.rs)

## Question
Can an unprivileged attacker who exercises a path whose behaviour is pinned by a shared constant, controlling the input that reaches the constant-governed path, drive `types` in `crates/primitives/src/types.rs` so that the constant a node compiles with and the constant the circuit compiles with stop being the same, breaking the invariant that protocol constants are single-sourced?

## Target
- File/function: `crates/primitives/src/types.rs` -> `types`
- Entrypoint: unprivileged party exercises a path whose behaviour is pinned by a shared constant
- Attacker controls: the input that reaches the constant-governed path
- Exploit idea: constant drift between roles - reach `types` from that entrypoint and force the divergence where the constant a node compiles with and the constant the circuit compiles with stop being the same; the adjacent symbols in the same file that carry the value are none adjacent, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: protocol constants are single-sourced
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: assert equality across crates in a test
