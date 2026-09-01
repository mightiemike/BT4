# Q1813: fee history across rate change via `config` (gas_oracle.rs)

## Question
Can an unprivileged attacker who queries the gas oracle immediately after filling a block with its own transactions, controlling the fill pattern of its own transactions, drive `config` in `crates/ethereum-rpc/src/gas_price/gas_oracle.rs` so that the base fee history reported and the base fees the blocks actually carry stop being equal, breaking the invariant that fee reporting matches executed blocks?

## Target
- File/function: `crates/ethereum-rpc/src/gas_price/gas_oracle.rs` -> `config`
- Entrypoint: unprivileged party queries the gas oracle immediately after filling a block with its own transactions
- Attacker controls: the fill pattern of its own transactions
- Exploit idea: fee history across rate change - reach `config` from that entrypoint and force the divergence where the base fee history reported and the base fees the blocks actually carry stop being equal; the adjacent symbols in the same file that carry the value are `GasPriceOracleConfig`, `GasPriceOracle`, `GasPriceOracleResult`, `fee_history`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: fee reporting matches executed blocks
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: cross a fee-rate transition and diff reported versus stored headers
