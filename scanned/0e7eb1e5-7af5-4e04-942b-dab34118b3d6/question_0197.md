# Q0197: bridge deposit reentered from user code via `get_block_hash` (mod.rs)

## Question
Can an unprivileged attacker who calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA, controlling contract bytecode and calldata, drive `get_block_hash` in `crates/evm/src/evm/system_contracts/mod.rs` so that the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct, breaking the invariant that user code cannot re-enter deposit crediting?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `get_block_hash`
- Entrypoint: unprivileged party calls a Citrea system contract (`BitcoinLightClient`, `BridgeWrapper`, `WCBTC`) from an ordinary EOA
- Attacker controls: contract bytecode and calldata
- Exploit idea: bridge deposit reentered from user code - reach `get_block_hash` from that entrypoint and force the divergence where the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: user code cannot re-enter deposit crediting
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: re-enter `deposit` from a user contract and assert rejection
