# Q5262: access list / warm-cold accounting via `is_system_caller` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling revert timing inside the frame, drive `is_system_caller` in `crates/evm/src/evm/handler.rs` so that the gas charged for a slot and the gas the spec assigns for its access state stop being equal, breaking the invariant that access accounting matches the spec deterministically?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `is_system_caller`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: revert timing inside the frame
- Exploit idea: access list / warm-cold accounting - reach `is_system_caller` from that entrypoint and force the divergence where the gas charged for a slot and the gas the spec assigns for its access state stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: access accounting matches the spec deterministically
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay adversarial access lists in the guest
