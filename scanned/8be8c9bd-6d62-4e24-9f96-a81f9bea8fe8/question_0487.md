# Q0487: bridge deposit reentered from user code via `transfer_ownership` (mod.rs)

## Question
Can an unprivileged attacker who deploys a contract that re-enters a system contract during a system transaction's block, controlling the target system-contract address and selector, drive `transfer_ownership` in `crates/evm/src/evm/system_contracts/mod.rs` so that the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct, breaking the invariant that user code cannot re-enter deposit crediting?

## Target
- File/function: `crates/evm/src/evm/system_contracts/mod.rs` -> `transfer_ownership`
- Entrypoint: unprivileged party deploys a contract that re-enters a system contract during a system transaction's block
- Attacker controls: the target system-contract address and selector
- Exploit idea: bridge deposit reentered from user code - reach `transfer_ownership` from that entrypoint and force the divergence where the deposit credit path a user contract can reach and the path reserved for system transactions stop being distinct; the adjacent symbols in the same file that carry the value are `BitcoinLightClient`, `BridgeWrapper`, `ProxyAdmin`, `WCBTC`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: user code cannot re-enter deposit crediting
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: re-enter `deposit` from a user contract and assert rejection
