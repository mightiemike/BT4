# Q2173: fee history across rate change via `suggest_tip_cap` (gas_oracle.rs)

## Question
Can an unprivileged attacker who calls `eth_feeHistory` / `eth_gasPrice` across an L1 fee-rate transition, controlling the block range requested, drive `suggest_tip_cap` in `crates/ethereum-rpc/src/gas_price/gas_oracle.rs` so that the base fee history reported and the base fees the blocks actually carry stop being equal, breaking the invariant that fee reporting matches executed blocks?

## Target
- File/function: `crates/ethereum-rpc/src/gas_price/gas_oracle.rs` -> `suggest_tip_cap`
- Entrypoint: unprivileged party calls `eth_feeHistory` / `eth_gasPrice` across an L1 fee-rate transition
- Attacker controls: the block range requested
- Exploit idea: fee history across rate change - reach `suggest_tip_cap` from that entrypoint and force the divergence where the base fee history reported and the base fees the blocks actually carry stop being equal; the adjacent symbols in the same file that carry the value are `GasPriceOracleConfig`, `GasPriceOracle`, `GasPriceOracleResult`, `config`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee reporting matches executed blocks
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: cross a fee-rate transition and diff reported versus stored headers
