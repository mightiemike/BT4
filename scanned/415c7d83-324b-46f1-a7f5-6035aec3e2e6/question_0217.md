# Q0217: vault withdrawal accounting via `get_system_caller` (mod.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `get_system_caller` in `crates/evm/src/evm/system_contracts/mod.rs` so that the balance a fee vault reports and the fees actually routed to it stop being equal, breaking the invariant that vault balances equal routed fees?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `get_system_caller`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: vault withdrawal accounting - reach `get_system_caller` from that entrypoint and force the divergence where the balance a fee vault reports and the fees actually routed to it stop being equal; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: vault balances equal routed fees
- Expected Immunefi impact: Critical - direct loss of user or vault funds
- Fast validation: sum routed fees across a block and compare
