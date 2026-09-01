# Q0919: state diff accumulation via `finalize_hook` (hooks_impl.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the fork-activation height it targets, drive `finalize_hook` in `crates/citrea-stf/src/hooks_impl.rs` so that the state diff the circuit outputs and the diff the DA blob carries stop being the same diff, breaking the invariant that published diffs equal proved diffs?

## Target
- File/function: `crates/citrea-stf/src/hooks_impl.rs` -> `finalize_hook`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the fork-activation height it targets
- Exploit idea: state diff accumulation - reach `finalize_hook` from that entrypoint and force the divergence where the state diff the circuit outputs and the diff the DA blob carries stop being the same diff; the adjacent symbols in the same file that carry the value are `pre_dispatch_tx_hook`, `post_dispatch_tx_hook`, `begin_l2_block_hook`, `end_l2_block_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: published diffs equal proved diffs
- Expected Immunefi impact: Critical - forged DA inclusion/completeness: a blob hidden from or injected into the proved block set
- Fast validation: diff the published blob against the circuit output
