# Q0797: vault withdrawal accounting via `empty_code` (genesis.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling value and gas, drive `empty_code` in `crates/evm/src/genesis.rs` so that the balance a fee vault reports and the fees actually routed to it stop being equal, breaking the invariant that vault balances equal routed fees?

## Target
- File/function: `crates/evm/src/genesis.rs` -> `empty_code`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: value and gas
- Exploit idea: vault withdrawal accounting - reach `empty_code` from that entrypoint and force the divergence where the balance a fee vault reports and the fees actually routed to it stop being equal; the adjacent symbols in the same file that carry the value are `AccountData`, `AccountDataHelper`, `EvmConfig`, `balance`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: vault balances equal routed fees
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum routed fees across a block and compare
