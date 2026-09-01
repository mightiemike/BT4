# Q2471: coinbase/basefee exposure via `run_interpreter` (handler.rs)

## Question
Can an unprivileged attacker who chains nested frames that touch balances, gas refunds and access lists in one transaction, controlling calldata entropy, drive `run_interpreter` in `crates/evm/src/evm/handler.rs` so that the block fields exposed to contracts and the fields the header commits stop being equal, breaking the invariant that EVM block context equals the sealed header?

## Target
- File/function: `crates/evm/src/evm/handler.rs` -> `run_interpreter`
- Entrypoint: unprivileged party chains nested frames that touch balances, gas refunds and access lists in one transaction
- Attacker controls: calldata entropy
- Exploit idea: coinbase/basefee exposure - reach `run_interpreter` from that entrypoint and force the divergence where the block fields exposed to contracts and the fields the header commits stop being equal; the adjacent symbols in the same file that carry the value are `TxInfo`, `CitreaChainExt`, `CitreaChain`, `CitreaCallExt`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: EVM block context equals the sealed header
- Expected Immunefi impact: High - transient consensus failure / unintended chain split recoverable only by resync
- Fast validation: read every block opcode from a contract and diff against the header
