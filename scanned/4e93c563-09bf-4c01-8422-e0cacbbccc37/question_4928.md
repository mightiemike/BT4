# Q4928: genesis config drift via `pre_dispatch_tx_hook` (hooks_impl.rs)

## Question
Can an unprivileged attacker who sends a transaction at an exact fork activation height, controlling the size and shape of the state diff, drive `pre_dispatch_tx_hook` in `crates/citrea-stf/src/hooks_impl.rs` so that the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal, breaking the invariant that genesis is identical across all roles?

## Target
- File/function: `crates/citrea-stf/src/hooks_impl.rs` -> `pre_dispatch_tx_hook`
- Entrypoint: unprivileged party sends a transaction at an exact fork activation height
- Attacker controls: the size and shape of the state diff
- Exploit idea: genesis config drift - reach `pre_dispatch_tx_hook` from that entrypoint and force the divergence where the genesis state the node initialises and the genesis root the circuit is compiled against stop being equal; the adjacent symbols in the same file that carry the value are `post_dispatch_tx_hook`, `begin_l2_block_hook`, `end_l2_block_hook`, `begin_slot_hook`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: genesis is identical across all roles
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: hash both and compare
