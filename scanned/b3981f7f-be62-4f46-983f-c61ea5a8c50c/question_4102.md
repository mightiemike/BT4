# Q4102: coinbase/basefee exposure via `ctx` (handler.rs)

## Question
Can an unprivileged attacker who deploys a contract and calls it in the same L2 block, controlling calldata entropy, drive `ctx` in `crates/evm/src/evm/handler.rs` so that the block fields exposed to contracts and the fields the header commits stop being equal, breaking the invariant that EVM block context equals the sealed header?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `ctx`
- Entrypoint: unprivileged party deploys a contract and calls it in the same L2 block
- Attacker controls: calldata entropy
- Exploit idea: coinbase/basefee exposure - reach `ctx` from that entrypoint and force the divergence where the block fields exposed to contracts and the fields the header commits stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: EVM block context equals the sealed header
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: read every block opcode from a contract and diff against the header
