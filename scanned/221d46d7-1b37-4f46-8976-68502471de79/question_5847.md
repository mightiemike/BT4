# Q5847: access list / warm-cold accounting via `diff_size_send_eth_eoa` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling contract bytecode and calldata, drive `diff_size_send_eth_eoa` in `crates/evm/src/evm/handler.rs` so that the gas charged for a slot and the gas the spec assigns for its access state stop being equal, breaking the invariant that access accounting matches the spec deterministically?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `diff_size_send_eth_eoa`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: contract bytecode and calldata
- Exploit idea: access list / warm-cold accounting - reach `diff_size_send_eth_eoa` from that entrypoint and force the divergence where the gas charged for a slot and the gas the spec assigns for its access state stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: access accounting matches the spec deterministically
- Expected Immunefi impact: Critical - unintended permanent chain split: honest nodes accept a state root the proved chain does not contain
- Fast validation: replay adversarial access lists in the guest
