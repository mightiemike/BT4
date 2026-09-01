# Q0893: fee history across rate change via `resolution` (fee_history.rs)

## Question
Can an unprivileged attacker who calls `eth_feeHistory` / `eth_gasPrice` across an L1 fee-rate transition, controlling the fill pattern of its own transactions, drive `resolution` in `crates/ethereum-rpc/src/gas_price/fee_history.rs` so that the base fee history reported and the base fees the blocks actually carry stop being equal, breaking the invariant that fee reporting matches executed blocks?

## Target
- File/function: `crates/ethereum-rpc/src/gas_price/fee_history.rs` -> `resolution`
- Entrypoint: unprivileged party calls `eth_feeHistory` / `eth_gasPrice` across an L1 fee-rate transition
- Attacker controls: the fill pattern of its own transactions
- Exploit idea: fee history across rate change - reach `resolution` from that entrypoint and force the divergence where the base fee history reported and the base fees the blocks actually carry stop being equal; the adjacent symbols in the same file that carry the value are `FeeHistoryCacheConfig`, `FeeHistoryCache`, `FeeHistoryEntry`, `config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee reporting matches executed blocks
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: cross a fee-rate transition and diff reported versus stored headers
